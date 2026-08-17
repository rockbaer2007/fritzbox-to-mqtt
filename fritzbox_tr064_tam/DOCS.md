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

The switch entity calls TR-064 `SetEnable`, so `AB0 Ein/Aus` can enable or disable the corresponding answering machine.
WLAN switches also call TR-064 `SetEnable`; for example `WLAN1 Ein/Aus` controls `WLANConfiguration:1`.

If your FRITZ!Box uses HTTPS TR-064, set:

```yaml
fritz_port: 49443
fritz_ssl: true
```
