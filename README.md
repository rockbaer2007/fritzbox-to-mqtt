# FRITZ!Box to MQTT

Home Assistant add-on repository for publishing FRITZ!Box data to Home Assistant through MQTT Discovery.

The add-on combines FRITZ!Box TR-064 calls, FRITZ!Box web/Lua fallbacks and the live call monitor on port `1012`. It creates MQTT Discovery entities for answering machines, WLAN switches, WAN state, call lists, phonebooks, live call events and optional DECT handset details.

## Installation

1. Open Home Assistant.
2. Go to **Settings > Add-ons > Add-on Store**.
3. Open the three-dot menu and choose **Repositories**.
4. Add this repository URL:

   ```text
   https://github.com/rockbaer2007/fritzbox-to-mqtt
   ```

5. Install **FRITZ!Box to MQTT**.
6. Configure the FRITZ!Box IP address, port, user and password.
7. Start the add-on.

The MQTT broker is discovered through the Home Assistant add-on service API when the Mosquitto broker add-on is available.

## HACS

This project is a Home Assistant Supervisor add-on, not a HACS custom integration or frontend card. HACS does not install Docker-based Supervisor add-ons. Use the Home Assistant Add-on Store repository flow above.

## Features

- Answering machine sensors and switches for `AB0` to `AB4`.
- WLAN 2.4 GHz, WLAN 5 GHz and guest WLAN switches where exposed by the FRITZ!Box.
- WAN connection speed and current upload/download rate.
- External IPv4/IPv6 values where available.
- Box state sensors such as mesh role, PPP connection, DECT base and DNS over TLS.
- Call list sensors for all, incoming, outgoing, missed, rejected and blocked calls.
- Live call monitor entities for `RING`, `CALL`, `CONNECT` and `DISCONNECT`.
- Phonebook sensors and selectable phonebook display.
- Optional DECT handset `intern` and `device` sensors with FRITZ!Box handset names.

## Add-on

The add-on lives in [`fritzbox_tr064_tam`](./fritzbox_tr064_tam). The folder and some unique IDs intentionally keep the original internal name for upgrade compatibility.

## License

MIT
