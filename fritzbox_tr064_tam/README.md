# FritzBox TR-064 TAM MQTT

Home Assistant add-on for reading FRITZ!Box answering machines through TR-064 and publishing the result through MQTT Discovery.

It creates entities for detected answering machines only. The entity names use `AB0` to `AB4`; the FRITZ!Box answering machine name is exposed only as an attribute.

## Entities

For each detected answering machine:

- `AB0 Neue Nachrichten`
- `AB0 Alte Nachrichten`
- `AB0 Ein/Aus`
- `AB0 Aktiv`

WAN entities:

- `Verbindung Download`
- `Verbindung Upload`
- `Downloadrate`
- `Uploadrate`
- `WAN Link Status`

Detected WLAN services:

- `WLAN1 Ein/Aus`
- `WLAN1 Status`

The SSID is exposed as an attribute and is not used as the entity name.

## Requirements

- TR-064 enabled on the FRITZ!Box.
- A FRITZ!Box user with sufficient rights for telephony/TAM and network status.
- MQTT broker reachable from the add-on.
- MQTT integration with discovery enabled in Home Assistant.
