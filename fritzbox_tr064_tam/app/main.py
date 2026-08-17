from __future__ import annotations

import json
import logging
import os
import re
import signal
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

import paho.mqtt.client as mqtt
import requests
from requests.auth import HTTPDigestAuth


LOG = logging.getLogger("fritzbox_tr064_tam")
SOAP_ENV = "http://schemas.xmlsoap.org/soap/envelope/"
TAM_SERVICE = "urn:dslforum-org:service:X_AVM-DE_TAM:1"
ONTEL_SERVICE = "urn:dslforum-org:service:X_AVM-DE_OnTel:1"
WAN_COMMON_SERVICE = "urn:dslforum-org:service:WANCommonInterfaceConfig:1"
WLAN_SERVICE_TEMPLATE = "urn:dslforum-org:service:WLANConfiguration:{index}"
CALL_VIEW_LABELS = {
    "all": "Alle Anrufe",
    "incoming": "Eingehende Anrufe",
    "outgoing": "Ausgehende Anrufe",
    "missed": "Verpasste Anrufe",
}
CALL_TYPE_VIEWS = {
    "1": "incoming",
    "2": "missed",
    "3": "outgoing",
}
WLAN_ROLES = {
    1: ("wlan2_4", "WLAN 2.4 GHz"),
    2: ("wlan5", "WLAN 5 GHz"),
    3: ("wlanguest", "WLAN Gast"),
}


@dataclass(frozen=True)
class Options:
    fritz_host: str
    fritz_port: int
    fritz_ssl: bool
    fritz_username: str
    fritz_password: str
    mqtt_host: str
    mqtt_port: int
    mqtt_username: str
    mqtt_password: str
    discovery_prefix: str
    base_topic: str
    poll_interval: int
    max_tam: int
    max_wlan: int
    call_lists: str
    phonebooks: str
    max_calls: int
    retain: bool


@dataclass(frozen=True)
class TamInfo:
    index: int
    present: bool
    enabled: bool
    running: bool
    name: str
    new_messages: int
    old_messages: int


@dataclass(frozen=True)
class WlanInfo:
    index: int
    enabled: bool
    status: str
    ssid: str


@dataclass(frozen=True)
class CallEntry:
    type_id: str
    view: str
    date: str
    name: str
    caller: str
    called: str
    number: str
    duration: str


@dataclass(frozen=True)
class PhonebookInfo:
    phonebook_id: str
    name: str
    contacts: list[dict[str, str]]


def wlan_slug(index: int) -> str:
    return WLAN_ROLES.get(index, (f"wlan_service_{index}", f"WLAN{index}"))[0]


def wlan_label(index: int) -> str:
    return WLAN_ROLES.get(index, (f"wlan_service_{index}", f"WLAN{index}"))[1]


def wlan_index_from_slug(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    for index, (slug, _label) in WLAN_ROLES.items():
        if value == slug:
            return index
    return None


class FritzBoxTr064Client:
    def __init__(self, options: Options) -> None:
        scheme = "https" if options.fritz_ssl else "http"
        self.base_url = f"{scheme}://{options.fritz_host}:{options.fritz_port}"
        self.session = requests.Session()
        self.session.auth = HTTPDigestAuth(options.fritz_username, options.fritz_password)
        self.session.verify = False

    def get_tam_info(self, index: int) -> TamInfo:
        info = self._soap("/upnp/control/x_tam", TAM_SERVICE, "GetInfo", {"NewIndex": index})
        name = str(info.get("NewName", "")).strip()
        enabled = as_bool(info.get("NewEnable"))
        running = as_bool(info.get("NewTAMRunning"))
        status = str(info.get("NewStatus", "")).strip().lower()
        try:
            new_messages, old_messages = self._get_message_counts(index)
        except Exception as exc:
            LOG.debug("Could not read AB%s message list: %s", index, exc)
            new_messages, old_messages = 0, 0
        present = bool(name) or status not in {"", "not_found", "unknown"} or new_messages > 0 or old_messages > 0
        return TamInfo(index, present, enabled, running, name, new_messages, old_messages)

    def set_tam_enabled(self, index: int, enabled: bool) -> None:
        self._soap(
            "/upnp/control/x_tam",
            TAM_SERVICE,
            "SetEnable",
            {"NewIndex": index, "NewEnable": 1 if enabled else 0},
        )

    def get_wlan_info(self, index: int) -> WlanInfo:
        info = self._soap(
            f"/upnp/control/wlanconfig{index}",
            WLAN_SERVICE_TEMPLATE.format(index=index),
            "GetInfo",
            {},
        )
        return WlanInfo(
            index=index,
            enabled=as_bool(info.get("NewEnable")),
            status=str(info.get("NewStatus", "")).strip() or "unknown",
            ssid=str(info.get("NewSSID", "")).strip(),
        )

    def set_wlan_enabled(self, index: int, enabled: bool) -> None:
        self._soap(
            f"/upnp/control/wlanconfig{index}",
            WLAN_SERVICE_TEMPLATE.format(index=index),
            "SetEnable",
            {"NewEnable": 1 if enabled else 0},
        )

    def get_wan_common(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        try:
            link = self._soap(
                "/upnp/control/wancommonifconfig1",
                WAN_COMMON_SERVICE,
                "GetCommonLinkProperties",
                {},
            )
            result["upstream_max_bps"] = as_int(link.get("NewLayer1UpstreamMaxBitRate"))
            result["downstream_max_bps"] = as_int(link.get("NewLayer1DownstreamMaxBitRate"))
            result["physical_link_status"] = str(link.get("NewPhysicalLinkStatus", "")).strip()
        except Exception as exc:
            LOG.warning("Could not read WAN link properties: %s", exc)

        try:
            addon = self._soap(
                "/upnp/control/wancommonifconfig1",
                WAN_COMMON_SERVICE,
                "GetAddonInfos",
                {},
            )
            result["byte_send_rate"] = as_int(addon.get("NewByteSendRate"))
            result["byte_receive_rate"] = as_int(addon.get("NewByteReceiveRate"))
        except Exception as exc:
            LOG.debug("Could not read WAN addon infos: %s", exc)

        return result

    def get_call_entries(self) -> list[CallEntry]:
        result = self._soap("/upnp/control/x_contact", ONTEL_SERVICE, "GetCallList", {})
        url = str(result.get("NewCallListURL", "")).strip()
        if not url:
            return []
        root = self._get_xml_url(url)
        entries: list[CallEntry] = []
        for call in root.findall(".//Call"):
            type_id = find_text(call, "Type").strip()
            caller = first_text(call, ["Caller", "Number"])
            called = first_text(call, ["Called", "CalledNumber"])
            name = find_text(call, "Name").strip()
            entries.append(CallEntry(
                type_id=type_id,
                view=CALL_TYPE_VIEWS.get(type_id, "other"),
                date=find_text(call, "Date").strip(),
                name=name,
                caller=caller,
                called=called,
                number=caller or called,
                duration=find_text(call, "Duration").strip(),
            ))
        return entries

    def get_phonebook_ids(self) -> list[str]:
        result = self._soap("/upnp/control/x_contact", ONTEL_SERVICE, "GetPhonebookList", {})
        value = str(result.get("NewPhonebookList", "")).strip()
        return [item.strip() for item in value.split(",") if item.strip()]

    def get_phonebook_info(self, phonebook_id: str) -> PhonebookInfo:
        result = self._soap(
            "/upnp/control/x_contact",
            ONTEL_SERVICE,
            "GetPhonebook",
            {"NewPhonebookID": phonebook_id},
        )
        url = str(result.get("NewPhonebookURL", "")).strip()
        if not url:
            return PhonebookInfo(phonebook_id, f"Telefonbuch {phonebook_id}", [])
        root = self._get_xml_url(url)
        name = root.attrib.get("name") or find_text(root, "Name") or f"Telefonbuch {phonebook_id}"
        contacts: list[dict[str, str]] = []
        for contact in root.findall(".//contact"):
            person = contact.find("person")
            display_name = find_text(person, "realName") if person is not None else ""
            numbers = []
            for number in contact.findall(".//number"):
                value = (number.text or "").strip()
                if value:
                    numbers.append(value)
            contacts.append({
                "name": display_name.strip(),
                "numbers": ", ".join(numbers),
            })
        return PhonebookInfo(phonebook_id, name.strip(), contacts)

    def _get_message_counts(self, index: int) -> tuple[int, int]:
        result = self._soap(
            "/upnp/control/x_tam",
            TAM_SERVICE,
            "GetMessageList",
            {"NewIndex": index},
        )
        url = str(result.get("NewURL", "")).strip()
        if not url:
            return 0, 0
        url = urllib.parse.urljoin(self.base_url, url)
        response = self.session.get(url, timeout=15)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        new_count = 0
        old_count = 0
        for message in root.findall(".//Message"):
            is_new = find_text(message, "New")
            if as_bool(is_new):
                new_count += 1
            else:
                old_count += 1
        return new_count, old_count

    def _get_xml_url(self, url: str) -> ET.Element:
        response = self.session.get(urllib.parse.urljoin(self.base_url, url), timeout=15)
        response.raise_for_status()
        return ET.fromstring(response.content)

    def _soap(
        self,
        control_url: str,
        service_type: str,
        action: str,
        arguments: dict[str, Any],
    ) -> dict[str, str]:
        body_args = "".join(f"<{key}>{escape_xml(value)}</{key}>" for key, value in arguments.items())
        envelope = (
            '<?xml version="1.0" encoding="utf-8"?>'
            f'<s:Envelope xmlns:s="{SOAP_ENV}" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
            "<s:Body>"
            f'<u:{action} xmlns:u="{service_type}">{body_args}</u:{action}>'
            "</s:Body>"
            "</s:Envelope>"
        )
        response = self.session.post(
            f"{self.base_url}{control_url}",
            data=envelope.encode("utf-8"),
            headers={
                "Content-Type": 'text/xml; charset="utf-8"',
                "SOAPACTION": f'"{service_type}#{action}"',
            },
            timeout=15,
        )
        response.raise_for_status()
        root = ET.fromstring(response.content)
        values: dict[str, str] = {}
        for element in root.iter():
            if element.text is not None and element.tag.split("}")[-1].startswith("New"):
                values[element.tag.split("}")[-1]] = element.text
        return values


class HomeAssistantMqttPublisher:
    def __init__(self, options: Options, fritz: FritzBoxTr064Client) -> None:
        self.options = options
        self.fritz = fritz
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="fritzbox-tr064-tam")
        if options.mqtt_username:
            self.client.username_pw_set(options.mqtt_username, options.mqtt_password)
        self.known_tam_indices: set[int] = set()
        self.known_wlan_indices: set[int] = set()
        self.known_call_views: set[str] = set()
        self.known_phonebook_ids: set[str] = set()
        self.selected_phonebook = self.options.phonebooks.strip() or "all"

    def start(self) -> None:
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.connect(self.options.mqtt_host, self.options.mqtt_port, keepalive=60)
        self.client.loop_start()

    def stop(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()

    def publish_discovery(
        self,
        present_tam_indices: set[int],
        present_wlan_indices: set[int],
        call_views: set[str],
        phonebook_ids: set[str],
        all_phonebooks: list[PhonebookInfo],
    ) -> None:
        for index in range(self.options.max_tam):
            if index in present_tam_indices:
                self._publish_tam_discovery(index)
            else:
                self._remove_tam_discovery(index)
        for index in range(1, self.options.max_wlan + 1):
            if index in present_wlan_indices:
                self._publish_wlan_discovery(index)
            else:
                self._remove_wlan_discovery(index)
        for view in call_views:
            self._publish_call_discovery(view)
        for view in self.known_call_views - call_views:
            self._remove_call_discovery(view)
        for phonebook_id in phonebook_ids:
            self._publish_phonebook_discovery(phonebook_id)
        for phonebook_id in self.known_phonebook_ids - phonebook_ids:
            self._remove_phonebook_discovery(phonebook_id)
        self._publish_phonebook_overview_discovery()
        self._publish_phonebook_select_discovery(all_phonebooks)
        self._publish_wan_discovery()
        self.known_tam_indices = set(present_tam_indices)
        self.known_wlan_indices = set(present_wlan_indices)
        self.known_call_views = set(call_views)
        self.known_phonebook_ids = set(phonebook_ids)

    def publish_states(
        self,
        tam_infos: list[TamInfo],
        wlan_infos: list[WlanInfo],
        wan: dict[str, Any],
        calls: list[CallEntry],
        call_views: set[str],
        phonebooks: list[PhonebookInfo],
        all_phonebooks: list[PhonebookInfo],
    ) -> None:
        for info in tam_infos:
            prefix = f"{self.options.base_topic}/ab/{info.index}"
            self._publish(f"{prefix}/new_messages", str(info.new_messages))
            self._publish(f"{prefix}/old_messages", str(info.old_messages))
            self._publish(f"{prefix}/enabled", "ON" if info.enabled else "OFF")
            self._publish(f"{prefix}/running", "ON" if info.running else "OFF")
            self._publish_json(f"{prefix}/attributes", {"ab_index": info.index, "ab_name": info.name})

        for info in wlan_infos:
            slug = wlan_slug(info.index)
            prefix = f"{self.options.base_topic}/wlan/{slug}"
            self._publish(f"{prefix}/enabled", "ON" if info.enabled else "OFF")
            self._publish(f"{prefix}/status", info.status)
            self._publish_json(
                f"{prefix}/attributes",
                {"wlan_index": info.index, "wlan_slug": slug, "ssid": info.ssid},
            )

        for view in sorted(call_views):
            filtered = calls if view == "all" else [call for call in calls if call.view == view]
            visible = filtered[:self.options.max_calls]
            prefix = f"{self.options.base_topic}/calls/{view}"
            self._publish(f"{prefix}/count", str(len(filtered)))
            self._publish_json(f"{prefix}/attributes", {
                "view": view,
                "max_calls": self.options.max_calls,
                "calls": [call_to_dict(call) for call in visible],
            })

        for phonebook in phonebooks:
            prefix = f"{self.options.base_topic}/phonebook/{safe_object_part(phonebook.phonebook_id)}"
            self._publish(f"{prefix}/count", str(len(phonebook.contacts)))
            self._publish_json(f"{prefix}/attributes", {
                "phonebook_id": phonebook.phonebook_id,
                "phonebook_name": phonebook.name,
                "contacts": phonebook.contacts[:50],
            })

        self._publish(f"{self.options.base_topic}/phonebooks/count", str(len(all_phonebooks)))
        self._publish(f"{self.options.base_topic}/phonebooks/selection", phonebook_selection_label(self.selected_phonebook, all_phonebooks))
        self._publish_json(f"{self.options.base_topic}/phonebooks/attributes", {
            "selected": self.selected_phonebook,
            "phonebooks": [phonebook_summary(phonebook) for phonebook in all_phonebooks],
        })

        if "upstream_max_bps" in wan:
            self._publish(f"{self.options.base_topic}/wan/upstream_max_mbit", format_mbit(wan["upstream_max_bps"]))
        if "downstream_max_bps" in wan:
            self._publish(f"{self.options.base_topic}/wan/downstream_max_mbit", format_mbit(wan["downstream_max_bps"]))
        if "byte_send_rate" in wan:
            self._publish(f"{self.options.base_topic}/wan/upload_kbit_s", format_kbit_per_second(wan["byte_send_rate"]))
        if "byte_receive_rate" in wan:
            self._publish(f"{self.options.base_topic}/wan/download_kbit_s", format_kbit_per_second(wan["byte_receive_rate"]))
        if "physical_link_status" in wan:
            self._publish(f"{self.options.base_topic}/wan/link_status", str(wan["physical_link_status"]))

    def _on_connect(self, client: mqtt.Client, _userdata: Any, _flags: Any, reason_code: Any, _properties: Any) -> None:
        LOG.info("Connected to MQTT broker with result %s", reason_code)
        client.subscribe(f"{self.options.base_topic}/ab/+/enabled/set")
        client.subscribe(f"{self.options.base_topic}/wlan/+/enabled/set")
        client.subscribe(f"{self.options.base_topic}/phonebooks/selection/set")

    def _on_message(self, _client: mqtt.Client, _userdata: Any, message: mqtt.MQTTMessage) -> None:
        topic = message.topic
        raw_payload = message.payload.decode("utf-8", errors="replace").strip()
        payload = raw_payload.upper()
        if topic.startswith(f"{self.options.base_topic}/ab/") and topic.endswith("/enabled/set"):
            self._handle_tam_command(topic, payload)
            return
        if topic.startswith(f"{self.options.base_topic}/wlan/") and topic.endswith("/enabled/set"):
            self._handle_wlan_command(topic, payload)
            return
        if topic == f"{self.options.base_topic}/phonebooks/selection/set":
            self._handle_phonebook_selection(raw_payload)

    def _handle_tam_command(self, topic: str, payload: str) -> None:
        marker = f"{self.options.base_topic}/ab/"
        if not topic.startswith(marker) or not topic.endswith("/enabled/set"):
            return
        try:
            index = int(topic[len(marker):].split("/", 1)[0])
        except ValueError:
            LOG.warning("Ignoring invalid TAM command topic: %s", topic)
            return
        if index < 0 or index >= self.options.max_tam:
            LOG.warning("Ignoring TAM command for unsupported index %s", index)
            return
        enabled = payload in {"ON", "1", "TRUE"}
        LOG.info("Setting AB%s enabled=%s", index, enabled)
        try:
            self.fritz.set_tam_enabled(index, enabled)
            self._publish(f"{self.options.base_topic}/ab/{index}/enabled", "ON" if enabled else "OFF")
        except Exception as exc:
            LOG.error("Could not set AB%s enabled state: %s", index, exc)

    def _handle_wlan_command(self, topic: str, payload: str) -> None:
        marker = f"{self.options.base_topic}/wlan/"
        slug = topic[len(marker):].split("/", 1)[0]
        index = wlan_index_from_slug(slug)
        if index is None:
            LOG.warning("Ignoring invalid WLAN command topic: %s", topic)
            return
        if index < 1 or index > self.options.max_wlan:
            LOG.warning("Ignoring WLAN command for unsupported index %s", index)
            return
        enabled = payload in {"ON", "1", "TRUE"}
        LOG.info("Setting %s enabled=%s", wlan_label(index), enabled)
        try:
            self.fritz.set_wlan_enabled(index, enabled)
            self._publish(f"{self.options.base_topic}/wlan/{wlan_slug(index)}/enabled", "ON" if enabled else "OFF")
        except Exception as exc:
            LOG.error("Could not set %s enabled state: %s", wlan_label(index), exc)

    def _handle_phonebook_selection(self, payload: str) -> None:
        selection = phonebook_selection_value(payload)
        self.selected_phonebook = selection
        self._publish(f"{self.options.base_topic}/phonebooks/selection", payload)
        LOG.info("Selected phonebook display: %s", selection)

    def _publish_tam_discovery(self, index: int) -> None:
        prefix = f"{self.options.base_topic}/ab/{index}"
        self._publish_config("sensor", f"ab{index}_new_messages", {
            "name": f"AB{index} Neue Nachrichten",
            "unique_id": f"fritzbox_tr064_ab{index}_new_messages",
            "state_topic": f"{prefix}/new_messages",
            "json_attributes_topic": f"{prefix}/attributes",
            "icon": "mdi:voicemail",
            "state_class": "measurement",
            "device": self._device(),
        })
        self._publish_config("sensor", f"ab{index}_old_messages", {
            "name": f"AB{index} Alte Nachrichten",
            "unique_id": f"fritzbox_tr064_ab{index}_old_messages",
            "state_topic": f"{prefix}/old_messages",
            "json_attributes_topic": f"{prefix}/attributes",
            "icon": "mdi:voicemail",
            "state_class": "measurement",
            "device": self._device(),
        })
        self._publish_config("switch", f"ab{index}_enabled", {
            "name": f"AB{index} Ein/Aus",
            "unique_id": f"fritzbox_tr064_ab{index}_enabled",
            "state_topic": f"{prefix}/enabled",
            "command_topic": f"{prefix}/enabled/set",
            "json_attributes_topic": f"{prefix}/attributes",
            "payload_on": "ON",
            "payload_off": "OFF",
            "icon": "mdi:answering-machine",
            "device": self._device(),
        })
        self._publish_config("binary_sensor", f"ab{index}_running", {
            "name": f"AB{index} Aktiv",
            "unique_id": f"fritzbox_tr064_ab{index}_running",
            "state_topic": f"{prefix}/running",
            "json_attributes_topic": f"{prefix}/attributes",
            "payload_on": "ON",
            "payload_off": "OFF",
            "icon": "mdi:phone-in-talk",
            "device": self._device(),
        })

    def _remove_tam_discovery(self, index: int) -> None:
        for component, suffix in [
            ("sensor", "new_messages"),
            ("sensor", "old_messages"),
            ("switch", "enabled"),
            ("binary_sensor", "running"),
        ]:
            self._publish(
                f"{self.options.discovery_prefix}/{component}/fritzbox_tr064/ab{index}_{suffix}/config",
                "",
                retain=True,
            )

    def _publish_wlan_discovery(self, index: int) -> None:
        slug = wlan_slug(index)
        label = wlan_label(index)
        prefix = f"{self.options.base_topic}/wlan/{slug}"
        self._remove_legacy_wlan_discovery(index)
        self._publish_config("switch", f"{slug}_enabled", {
            "name": f"{label} Ein/Aus",
            "unique_id": f"fritzbox_tr064_{slug}_enabled",
            "state_topic": f"{prefix}/enabled",
            "command_topic": f"{prefix}/enabled/set",
            "json_attributes_topic": f"{prefix}/attributes",
            "payload_on": "ON",
            "payload_off": "OFF",
            "icon": "mdi:wifi",
            "device": self._device(),
        })
        self._publish_config("sensor", f"{slug}_status", {
            "name": f"{label} Status",
            "unique_id": f"fritzbox_tr064_{slug}_status",
            "state_topic": f"{prefix}/status",
            "json_attributes_topic": f"{prefix}/attributes",
            "icon": "mdi:wifi-settings",
            "device": self._device(),
        })

    def _remove_wlan_discovery(self, index: int) -> None:
        self._remove_legacy_wlan_discovery(index)
        for component, suffix in [
            ("switch", "enabled"),
            ("sensor", "status"),
        ]:
            self._publish(
                f"{self.options.discovery_prefix}/{component}/fritzbox_tr064/{wlan_slug(index)}_{suffix}/config",
                "",
                retain=True,
            )

    def _remove_legacy_wlan_discovery(self, index: int) -> None:
        for component, suffix in [
            ("switch", "enabled"),
            ("sensor", "status"),
        ]:
            self._publish(
                f"{self.options.discovery_prefix}/{component}/fritzbox_tr064/wlan{index}_{suffix}/config",
                "",
                retain=True,
            )

    def _publish_call_discovery(self, view: str) -> None:
        label = CALL_VIEW_LABELS.get(view, f"Anrufe {view}")
        prefix = f"{self.options.base_topic}/calls/{view}"
        self._publish_config("sensor", f"calls_{view}", {
            "name": label,
            "unique_id": f"fritzbox_tr064_calls_{view}",
            "state_topic": f"{prefix}/count",
            "json_attributes_topic": f"{prefix}/attributes",
            "icon": "mdi:phone-log",
            "state_class": "measurement",
            "device": self._device(),
        })

    def _remove_call_discovery(self, view: str) -> None:
        self._publish(
            f"{self.options.discovery_prefix}/sensor/fritzbox_tr064/calls_{view}/config",
            "",
            retain=True,
        )

    def _publish_phonebook_discovery(self, phonebook_id: str) -> None:
        object_part = safe_object_part(phonebook_id)
        prefix = f"{self.options.base_topic}/phonebook/{object_part}"
        self._publish_config("sensor", f"phonebook_{object_part}", {
            "name": f"Telefonbuch {phonebook_id}",
            "unique_id": f"fritzbox_tr064_phonebook_{object_part}",
            "state_topic": f"{prefix}/count",
            "json_attributes_topic": f"{prefix}/attributes",
            "icon": "mdi:book-account",
            "state_class": "measurement",
            "device": self._device(),
        })

    def _remove_phonebook_discovery(self, phonebook_id: str) -> None:
        self._publish(
            f"{self.options.discovery_prefix}/sensor/fritzbox_tr064/phonebook_{safe_object_part(phonebook_id)}/config",
            "",
            retain=True,
        )

    def _publish_phonebook_overview_discovery(self) -> None:
        prefix = f"{self.options.base_topic}/phonebooks"
        self._publish_config("sensor", "phonebooks", {
            "name": "Telefonbücher",
            "unique_id": "fritzbox_tr064_phonebooks",
            "state_topic": f"{prefix}/count",
            "json_attributes_topic": f"{prefix}/attributes",
            "icon": "mdi:book-multiple",
            "state_class": "measurement",
            "device": self._device(),
        })

    def _publish_phonebook_select_discovery(self, phonebooks: list[PhonebookInfo]) -> None:
        prefix = f"{self.options.base_topic}/phonebooks"
        self._publish_config("select", "phonebook_selection", {
            "name": "Telefonbuch Anzeige",
            "unique_id": "fritzbox_tr064_phonebook_selection",
            "state_topic": f"{prefix}/selection",
            "command_topic": f"{prefix}/selection/set",
            "options": phonebook_select_options(phonebooks),
            "icon": "mdi:book-cog",
            "device": self._device(),
        })

    def _publish_wan_discovery(self) -> None:
        sensors = [
            ("wan_downstream_max_mbit", "Verbindung Download", "wan/downstream_max_mbit", "Mbit/s", "mdi:download-network"),
            ("wan_upstream_max_mbit", "Verbindung Upload", "wan/upstream_max_mbit", "Mbit/s", "mdi:upload-network"),
            ("wan_download_kbit_s", "Downloadrate", "wan/download_kbit_s", "kbit/s", "mdi:download"),
            ("wan_upload_kbit_s", "Uploadrate", "wan/upload_kbit_s", "kbit/s", "mdi:upload"),
        ]
        for object_id, name, state_path, unit, icon in sensors:
            self._publish_config("sensor", object_id, {
                "name": name,
                "unique_id": f"fritzbox_tr064_{object_id}",
                "state_topic": f"{self.options.base_topic}/{state_path}",
                "unit_of_measurement": unit,
                "state_class": "measurement",
                "icon": icon,
                "device": self._device(),
            })
        self._publish_config("sensor", "wan_link_status", {
            "name": "WAN Link Status",
            "unique_id": "fritzbox_tr064_wan_link_status",
            "state_topic": f"{self.options.base_topic}/wan/link_status",
            "icon": "mdi:wan",
            "device": self._device(),
        })

    def _publish_config(self, component: str, object_id: str, payload: dict[str, Any]) -> None:
        topic = f"{self.options.discovery_prefix}/{component}/fritzbox_tr064/{object_id}/config"
        self._publish_json(topic, payload, retain=True)

    def _publish_json(self, topic: str, payload: dict[str, Any], retain: bool | None = None) -> None:
        self._publish(topic, json.dumps(payload, separators=(",", ":")), retain=retain)

    def _publish(self, topic: str, payload: str, retain: bool | None = None) -> None:
        self.client.publish(topic, payload, qos=0, retain=self.options.retain if retain is None else retain)

    @staticmethod
    def _device() -> dict[str, Any]:
        return {
            "identifiers": ["fritzbox_tr064"],
            "name": "FRITZ!Box TR-064",
            "manufacturer": "AVM",
            "model": "FRITZ!Box",
        }


def run() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    options = load_options()
    stop_event = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_args: stop_event.set())
    signal.signal(signal.SIGINT, lambda *_args: stop_event.set())

    fritz = FritzBoxTr064Client(options)
    publisher = HomeAssistantMqttPublisher(options, fritz)
    publisher.start()

    try:
        while not stop_event.is_set():
            tam_infos: list[TamInfo] = []
            wlan_infos: list[WlanInfo] = []
            for index in range(options.max_tam):
                try:
                    info = fritz.get_tam_info(index)
                    if info.present:
                        tam_infos.append(info)
                except Exception as exc:
                    LOG.info("AB%s not available or not readable: %s", index, exc)
            for index in range(1, options.max_wlan + 1):
                try:
                    wlan_infos.append(fritz.get_wlan_info(index))
                except Exception as exc:
                    LOG.info("WLAN%s not available or not readable: %s", index, exc)
            try:
                calls = fritz.get_call_entries()
            except Exception as exc:
                LOG.info("Call list not available or not readable: %s", exc)
                calls = []
            try:
                all_phonebook_ids = fritz.get_phonebook_ids()
            except Exception as exc:
                LOG.info("Phonebooks not available or not readable: %s", exc)
                all_phonebook_ids = []
            all_phonebooks = []
            for phonebook_id in all_phonebook_ids:
                try:
                    all_phonebooks.append(fritz.get_phonebook_info(phonebook_id))
                except Exception as exc:
                    LOG.info("Phonebook %s not available or not readable: %s", phonebook_id, exc)
            selected_phonebook_ids = selected_phonebooks(
                publisher.selected_phonebook,
                [phonebook.phonebook_id for phonebook in all_phonebooks],
            )
            phonebooks = [phonebook for phonebook in all_phonebooks if phonebook.phonebook_id in selected_phonebook_ids]
            call_views = selected_call_views(options.call_lists)
            present_tam = {info.index for info in tam_infos}
            present_wlan = {info.index for info in wlan_infos}
            present_phonebooks = {phonebook.phonebook_id for phonebook in phonebooks}
            publisher.publish_discovery(present_tam, present_wlan, call_views, present_phonebooks, all_phonebooks)
            publisher.publish_states(tam_infos, wlan_infos, fritz.get_wan_common(), calls, call_views, phonebooks, all_phonebooks)
            LOG.info(
                "Published %s answering machines, %s WLAN services, %s call views, %s selected phonebooks, %s listed phonebooks and WAN state",
                len(tam_infos),
                len(wlan_infos),
                len(call_views),
                len(phonebooks),
                len(all_phonebooks),
            )
            stop_event.wait(options.poll_interval)
    finally:
        publisher.stop()


def load_options() -> Options:
    raw: dict[str, Any] = {}
    options_path = os.getenv("OPTIONS_PATH", "/data/options.json")
    if os.path.exists(options_path):
        with open(options_path, "r", encoding="utf-8") as file:
            raw = json.load(file)
    else:
        raw = {
            "fritz_host": os.getenv("FRITZ_HOST", "192.168.178.1"),
            "fritz_port": int(os.getenv("FRITZ_PORT", "49000")),
            "fritz_ssl": os.getenv("FRITZ_SSL", "false").lower() == "true",
            "fritz_username": os.getenv("FRITZ_USERNAME", ""),
            "fritz_password": os.getenv("FRITZ_PASSWORD", ""),
            "mqtt_host": os.getenv("MQTT_HOST", "127.0.0.1"),
            "mqtt_port": int(os.getenv("MQTT_PORT", "1883")),
            "mqtt_username": os.getenv("MQTT_USERNAME", ""),
            "mqtt_password": os.getenv("MQTT_PASSWORD", ""),
        }
    return Options(
        fritz_host=str(raw.get("ip", raw.get("fritz_host", "192.168.178.1"))),
        fritz_port=int(raw.get("port", raw.get("fritz_port", 49000))),
        fritz_ssl=bool(raw.get("fritz_ssl", False)),
        fritz_username=str(raw.get("user", raw.get("fritz_username", ""))),
        fritz_password=str(raw.get("password", raw.get("fritz_password", ""))),
        mqtt_host=str(os.getenv("MQTT_HOST", raw.get("mqtt_host", "core-mosquitto"))),
        mqtt_port=int(os.getenv("MQTT_PORT", raw.get("mqtt_port", 1883))),
        mqtt_username=str(os.getenv("MQTT_USERNAME", raw.get("mqtt_username", ""))),
        mqtt_password=str(os.getenv("MQTT_PASSWORD", raw.get("mqtt_password", ""))),
        discovery_prefix=str(raw.get("discovery_prefix", "homeassistant")).strip("/"),
        base_topic=str(raw.get("base_topic", "fritzbox/tr064")).strip("/"),
        poll_interval=int(raw.get("poll_interval", 60)),
        max_tam=max(1, min(5, int(raw.get("max_tam", 5)))),
        max_wlan=max(1, min(5, int(raw.get("max_wlan", 4)))),
        call_lists=str(raw.get("call_lists", "all,incoming,outgoing,missed")),
        phonebooks=str(raw.get("phonebooks", "all")),
        max_calls=max(1, min(100, int(raw.get("max_calls", 20)))),
        retain=bool(raw.get("retain", True)),
    )


def selected_call_views(value: str) -> set[str]:
    requested = {item.strip().lower() for item in value.split(",") if item.strip()}
    selected = requested & set(CALL_VIEW_LABELS)
    return selected or {"all"}


def selected_phonebooks(value: str, available_ids: list[str]) -> list[str]:
    requested = [phonebook_selection_value(item) for item in value.split(",") if item.strip()]
    if not requested or any(item.lower() == "all" for item in requested):
        return available_ids
    available = set(available_ids)
    return [item for item in requested if item in available]


def phonebook_select_options(phonebooks: list[PhonebookInfo]) -> list[str]:
    return ["Alle Telefonbücher"] + [phonebook_option_label(phonebook) for phonebook in phonebooks]


def phonebook_option_label(phonebook: PhonebookInfo) -> str:
    return f"{phonebook.phonebook_id}: {phonebook.name}"


def phonebook_selection_label(value: str, phonebooks: list[PhonebookInfo]) -> str:
    selection = phonebook_selection_value(value)
    if selection == "all":
        return "Alle Telefonbücher"
    for phonebook in phonebooks:
        if phonebook.phonebook_id == selection:
            return phonebook_option_label(phonebook)
    return value


def phonebook_selection_value(value: str) -> str:
    normalized = value.strip()
    if not normalized or normalized.lower() in {"all", "alle", "alle telefonbücher", "alle telefonbuecher"}:
        return "all"
    return normalized.split(":", 1)[0].strip()


def phonebook_summary(phonebook: PhonebookInfo) -> dict[str, Any]:
    return {
        "id": phonebook.phonebook_id,
        "name": phonebook.name,
        "contacts": len(phonebook.contacts),
    }


def call_to_dict(call: CallEntry) -> dict[str, str]:
    return {
        "type": call.view,
        "type_id": call.type_id,
        "date": call.date,
        "name": call.name,
        "caller": call.caller,
        "called": call.called,
        "number": call.number,
        "duration": call.duration,
    }


def safe_object_part(value: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_]+", "_", value.strip().lower())
    return safe.strip("_") or "unknown"


def first_text(element: ET.Element, names: list[str]) -> str:
    for name in names:
        value = find_text(element, name).strip()
        if value:
            return value
    return ""


def find_text(element: ET.Element, local_name: str) -> str:
    for child in element:
        if child.tag.split("}")[-1] == local_name:
            return child.text or ""
    return ""


def as_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def as_int(value: Any) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def format_mbit(bits_per_second: Any) -> str:
    return f"{as_int(bits_per_second) / 1_000_000:.2f}"


def format_kbit_per_second(bytes_per_second: Any) -> str:
    return f"{as_int(bytes_per_second) * 8 / 1_000:.2f}"


def escape_xml(value: Any) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


if __name__ == "__main__":
    run()
