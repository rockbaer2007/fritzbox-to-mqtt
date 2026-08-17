# Configuration

Example:

```yaml
fritz_host: fritz.box
fritz_port: 49000
fritz_ssl: false
fritz_username: homeassistant
fritz_password: secret
mqtt_host: core-mosquitto
mqtt_port: 1883
mqtt_username: ""
mqtt_password: ""
discovery_prefix: homeassistant
base_topic: fritzbox/tr064
poll_interval: 60
max_tam: 5
max_wlan: 4
retain: true
```

The add-on probes answering machine indexes `0` to `max_tam - 1` and publishes discovery only for readable/present entries.
It probes WLAN TR-064 services from `WLANConfiguration:1` to `WLANConfiguration:max_wlan` and publishes only services that answer successfully.
The first three WLAN services use stable MQTT and Home Assistant object IDs:

- `WLANConfiguration:1` -> `wlan2_4` / `WLAN 2.4 GHz`
- `WLANConfiguration:2` -> `wlan5` / `WLAN 5 GHz`
- `WLANConfiguration:3` -> `wlanguest` / `WLAN Gast`

Further readable WLAN services use the fallback `wlan_service_4`, `wlan_service_5`, and so on.

The switch entity calls TR-064 `SetEnable`, so `AB0 Ein/Aus` can enable or disable the corresponding answering machine.
WLAN switches also call TR-064 `SetEnable`; for example `WLAN 2.4 GHz Ein/Aus` controls `WLANConfiguration:1`.

If your FRITZ!Box uses HTTPS TR-064, set:

```yaml
fritz_port: 49443
fritz_ssl: true
```
