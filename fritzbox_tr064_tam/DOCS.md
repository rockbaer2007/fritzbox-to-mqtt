# Configuration

Example:

```yaml
ip: 192.168.178.1
port: 49000
user: homeassistant
password: secret
call_lists: all,incoming,outgoing,missed
phonebooks: all
max_calls: 20
```

The visible Home Assistant add-on configuration asks for the FRITZ!Box connection and display choices.
MQTT host, port, username and password are requested from Home Assistant's internal MQTT service automatically.
MQTT discovery uses the prefix `homeassistant`, base topic `fritzbox/tr064`, polling every 60 seconds, up to five answering machines and up to four WLAN services.

`call_lists` is a comma-separated selection of:

- `all`
- `incoming`
- `outgoing`
- `missed`

`phonebooks` can be `all` or a comma-separated list of FRITZ!Box phonebook IDs, for example `0,1`.
It is only the startup selection. After the first successful scan, the `Telefonbücher` sensor lists all detected phonebooks and the `Telefonbuch Anzeige` select entity can switch between `Alle Telefonbücher` and individual phonebooks.
`max_calls` limits how many calls are included in the sensor attributes. The sensor state still reports the total count for the selected list.

The add-on probes answering machine indexes `0` to `max_tam - 1` and publishes discovery only for readable/present entries.
It probes WLAN TR-064 services from `WLANConfiguration:1` to `WLANConfiguration:max_wlan` and publishes only services that answer successfully.
The first three WLAN services use stable MQTT and Home Assistant object IDs:

- `WLANConfiguration:1` -> `wlan2_4` / `WLAN 2.4 GHz`
- `WLANConfiguration:2` -> `wlan5` / `WLAN 5 GHz`
- `WLANConfiguration:3` -> `wlanguest` / `WLAN Gast`

Further readable WLAN services use the fallback `wlan_service_4`, `wlan_service_5`, and so on.

The switch entity calls TR-064 `SetEnable`, so `AB0 Ein/Aus` can enable or disable the corresponding answering machine.
WLAN switches also call TR-064 `SetEnable`; for example `WLAN 2.4 GHz Ein/Aus` controls `WLANConfiguration:1`.
