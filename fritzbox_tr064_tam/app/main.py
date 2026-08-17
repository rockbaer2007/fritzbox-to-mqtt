from __future__ import annotations

import json
import logging
import os
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
WAN_COMMON_SERVICE = "urn:dslforum-org:service:WANCommonInterfaceConfig:1"


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

    def start(self) -> None:
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.connect(self.options.mqtt_host, self.options.mqtt_port, keepalive=60)
        self.client.loop_start()

    def stop(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()

    def publish_discovery(self, present_indices: set[int]) -> None:
        for index in range(self.options.max_tam):
            if index in present_indices:
                self._publish_tam_discovery(index)
            else:
                self._remove_tam_discovery(index)
        self._publish_wan_discovery()
        self.known_tam_indices = set(present_indices)

    def publish_states(self, tam_infos: list[TamInfo], wan: dict[str, Any]) -> None:
        for info in tam_infos:
            prefix = f"{self.options.base_topic}/ab/{info.index}"
            self._publish(f"{prefix}/new_messages", str(info.new_messages))
            self._publish(f"{prefix}/old_messages", str(info.old_messages))
            self._publish(f"{prefix}/enabled", "ON" if info.enabled else "OFF")
            self._publish(f"{prefix}/running", "ON" if info.running else "OFF")
            self._publish_json(f"{prefix}/attributes", {"ab_index": info.index, "ab_name": info.name})

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

    def _on_message(self, _client: mqtt.Client, _userdata: Any, message: mqtt.MQTTMessage) -> None:
        topic = message.topic
        payload = message.payload.decode("utf-8", errors="replace").strip().upper()
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
                f"{self.options.discovery_prefix}/{component}/fritzbox_tr064_ab/ab{index}_{suffix}/config",
                "",
                retain=True,
            )

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
        topic = f"{self.options.discovery_prefix}/{component}/fritzbox_tr064_ab/{object_id}/config"
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
            for index in range(options.max_tam):
                try:
                    info = fritz.get_tam_info(index)
                    if info.present:
                        tam_infos.append(info)
                except Exception as exc:
                    LOG.info("AB%s not available or not readable: %s", index, exc)
            present = {info.index for info in tam_infos}
            publisher.publish_discovery(present)
            publisher.publish_states(tam_infos, fritz.get_wan_common())
            LOG.info("Published %s answering machines and WAN state", len(tam_infos))
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
            "fritz_host": os.getenv("FRITZ_HOST", "fritz.box"),
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
        fritz_host=str(raw.get("fritz_host", "fritz.box")),
        fritz_port=int(raw.get("fritz_port", 49000)),
        fritz_ssl=bool(raw.get("fritz_ssl", False)),
        fritz_username=str(raw.get("fritz_username", "")),
        fritz_password=str(raw.get("fritz_password", "")),
        mqtt_host=str(raw.get("mqtt_host", "core-mosquitto")),
        mqtt_port=int(raw.get("mqtt_port", 1883)),
        mqtt_username=str(raw.get("mqtt_username", "")),
        mqtt_password=str(raw.get("mqtt_password", "")),
        discovery_prefix=str(raw.get("discovery_prefix", "homeassistant")).strip("/"),
        base_topic=str(raw.get("base_topic", "fritzbox/tr064")).strip("/"),
        poll_interval=int(raw.get("poll_interval", 60)),
        max_tam=max(1, min(5, int(raw.get("max_tam", 5)))),
        retain=bool(raw.get("retain", True)),
    )


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
