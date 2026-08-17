# FritzBox TR-064 TAM MQTT

Home Assistant add-on for reading FRITZ!Box answering machines through TR-064 and publishing the result through MQTT Discovery.

It creates entities for detected answering machines only. The entity names use `AB0` to `AB4`; the FRITZ!Box answering machine name is exposed only as an attribute.

## Entities

For each detected answering machine:

- `AB0 Neue Nachrichten`
- `AB0 Alte Nachrichten`
- `AB0 Ein/Aus`
- `AB0 Status`

The AB status follows the FRITZ!Box `NewEnable` value. The raw `NewTAMRunning` and `NewStatus` values are exposed as attributes for diagnostics.

WAN entities:

- `Verbindung Download`
- `Verbindung Upload`
- `Downloadrate`
- `Uploadrate`
- `WAN Link Status`

Call list entities, depending on `call_lists`:

- `Alle Anrufe`
- `Eingehende Anrufe`
- `Ausgehende Anrufe`
- `Verpasste Anrufe`

Each call list sensor reports the total count as its state and exposes up to `max_calls` entries in the `calls` attribute.

Phonebook entities, depending on `phonebooks`:

- `Telefonbücher`
- `Telefonbuch Anzeige`
- detected FRITZ!Box phonebooks by their FRITZ!Box names
- further detected FRITZ!Box phonebooks

`Telefonbücher` lists all detected FRITZ!Box phonebooks in its attributes.
`Telefonbuch Anzeige` is a Home Assistant select entity for choosing `Alle Telefonbücher` or one detected phonebook.
Each selected phonebook sensor reports the contact count as its state and exposes the FRITZ!Box phonebook name as an attribute.

Detected WLAN services:

- `WLAN 2.4 GHz Ein/Aus` (`wlan2_4`)
- `WLAN 2.4 GHz Status` (`wlan2_4`)
- `WLAN 5 GHz Ein/Aus` (`wlan5`)
- `WLAN 5 GHz Status` (`wlan5`)
- `WLAN Gast Ein/Aus` (`wlanguest`)
- `WLAN Gast Status` (`wlanguest`)

The SSID is exposed as an attribute and is not used as the entity name.

## Requirements

- TR-064 enabled on the FRITZ!Box.
- A FRITZ!Box user with sufficient rights for telephony/TAM and network status.
- MQTT broker installed as Home Assistant add-on/app.
- MQTT integration with discovery enabled in Home Assistant.

## Add-on Configuration

The visible configuration mask contains only:

```yaml
ip: 192.168.178.1
port: 49000
user: homeassistant
password: secret
call_lists: all,incoming,outgoing,missed
phonebooks: all
max_calls: 20
```

MQTT host, port, username and password are requested from Home Assistant's internal MQTT service automatically.
`phonebooks` is the startup selection; after the first successful scan, use the `Telefonbuch Anzeige` select entity in Home Assistant.
