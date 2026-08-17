# FRITZ!Box to MQTT

Home Assistant app repository for publishing FRITZ!Box data to Home Assistant through MQTT Discovery.

The app combines FRITZ!Box TR-064 calls, FRITZ!Box web/Lua fallbacks and the live call monitor on port `1012`. It creates MQTT Discovery entities for answering machines, WLAN switches, WAN state, call lists, phonebooks, live call events and optional DECT handset details.

## Installation

[![Open your Home Assistant instance and add the FRITZ!Box to MQTT app repository](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Frockbaer2007%2Ffritzbox-to-mqtt)

1. Open Home Assistant.
2. Go to **Settings > Apps > App-Store**.
3. Open the three-dot menu and choose **Repositories**.
4. Add this repository URL:

   ```text
   https://github.com/rockbaer2007/fritzbox-to-mqtt
   ```

5. Install **FRITZ!Box to MQTT**.
6. Configure the FRITZ!Box IP address, port, user and password.
7. Start the app.

The MQTT broker is discovered through the Home Assistant app service API when the Mosquitto broker app is available.

## HACS

This project is a Home Assistant Supervisor app, not a HACS custom integration or frontend card. HACS does not install Docker-based Supervisor apps. Use the Home Assistant App-Store repository flow above.

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

## Examples

- [Mushroom status card](./examples/mushroom-status-card.yaml) for WAN rates, answering machines and WLAN status.

## App

The app lives in [`fritzbox_tr064_tam`](./fritzbox_tr064_tam). The folder and some unique IDs intentionally keep the original internal name for upgrade compatibility.

## License

MIT
