# Wechselrichter: Was liefert Modbus TCP, braucht es RTU?

Stand der Recherche: 10.11.2026, aus Hersteller-Leitfäden, Integrationsdokus (evcc, Home Assistant, openHAB, TapHome) und Foren. **Nicht an echten Geräten geprüft.** Bei der ersten Anlage jedes Herstellers mit `erkennen` und `geraet-test` bestätigen und unten nachtragen.

**Kurz:** Für die Dashboard-Daten (PV-Leistung, Netzbezug/-einspeisung, Batterieleistung, Ladezustand) ist bei keinem der sieben Hersteller Modbus RTU (RS485) zwingend. Mehrere brauchen aber eine Einstellung, ein Zusatzmodul oder einen separaten Zähler.

| Hersteller | Modbus TCP | Port / Unit-ID | PV | Netz | Batterie | Stolpersteine |
|---|---|---|---|---|---|---|
| **Fronius** GEN24, Symo, Primo | ja, eingebaut | 502 / WR 1, Zähler 200 | ja | ja, Smart Meter über Unit 200 | ja (Speicher-Block) | Im Webinterface «Modbus TCP Server» einschalten, SunSpec-Typ **int + SF**. Alternativ Solar API (HTTP), ganz ohne Modbus. |
| **SMA** Sunny Tripower, Sunny Boy | ja, eingebaut | 502 / SunSpec meist 126, SMA-Profil 3 | ja | **nicht bei allen Modellen über den WR** (z. B. Tripower X). Netzwerte dann vom **Sunny Home Manager 2** (Modbus TCP, Unit 2) oder vom SMA Energy Meter | je nach Modell | Modbus-Server im WR unter «Externe Kommunikation» aktivieren; beim Home Manager im Sunny Portal «Vorgaben über lokale Schnittstellen» freigeben. |
| **SolarEdge** | ja, eingebaut | **oft 1502**, sonst 502 / Unit 1 | ja | ja, SolarEdge-Zähler als SunSpec-Modell 203 | **nicht bei allen WR** über Modbus | In SetApp aktivieren. Ein Zähler mit Doppelfunktion meldet über Modbus nur eine Funktion. |
| **Kostal** Plenticore | ja, eingebaut | **1502** / **71** | ja | ja | ja (Ladezustand u. a.) | SunSpec- und Kostal-eigene Register; Byte-Reihenfolge je nach Einstellung (SunSpec-Modus = Standard). |
| **Huawei** SUN2000 | ja, über SDongle oder WR-WLAN | 502 / meist 1 | ja | ja (Leistungsmesser) | ja | **Nur ein Modbus-Client gleichzeitig.** Läuft schon Wallbox, evcc oder Home Assistant, blockieren sie sich. Dann Modbus-Proxy oder Shelly. Im Dongle «Modbus TCP: Enable (unrestricted)». |
| **GoodWe** ET/EH/BT/BH | **nur mit LAN- oder WiFi+LAN-Modul (Kit 2.0)** | 502 / 247 oder 1 | ja | ja | ja | Standard-WLAN-Modul spricht nur mit der Cloud (bzw. UDP 8899). Firmware laut GoodWe-Leitfaden ab ARM 13 (Hochvolt) bzw. 15 (Niedervolt). Ohne Modul: RS485 am EMS-Port plus Gateway, oder Shelly. |
| **Sigenergy** SigenStor, Sigen Hybrid | ja, eingebaut | 502 / Plant 247 | ja | ja | ja | In der mySigen-App «Modbus TCP Server» einschalten, evtl. durch den Installateur. SigenStor laut Integration ab Firmware SPC109. Abfrage höchstens ca. 1× pro Sekunde (Pi: alle 5 s). |

## Wann doch RS485 (RTU)?

Nur wenn ein Gerät kein Modbus TCP anbietet (z. B. GoodWe ohne LAN-Modul, ältere Geräte, viele Zähler). Die Pi-Software spricht nur Modbus TCP. Für RTU-Geräte gibt es zwei Wege:

1. **Modbus-RTU/TCP-Gateway** (Hutschiene, RS485 auf LAN, Modus «Modbus TCP zu RTU»). Der Pi spricht das Gateway wie ein normales Modbus-TCP-Gerät an, die Unit-ID ist die RS485-Adresse. Funktioniert mit allen Profilen ohne Programmänderung.
2. **Messung statt Protokoll:** Shelly Pro 3EM oder smart-me-Zähler am PV-Abgang und am Netzanschluss. Liefert PV und Netz für jede Marke, aber keinen Batterie-Ladezustand.

Eine RS485-Platine direkt am Pi ist nicht nötig und wird von der Software zurzeit nicht genutzt.

## Was die Geräteerkennung dafür prüft

- Ports **502 und 1502**
- SunSpec-Unit-IDs 1, 2, 3, **71**, 100, 126, 200, 240, 247
- Herstellerprofile: Huawei (Unit 1, 0), GoodWe (Unit 247, 1), Sigenergy (Unit 247)

## Quellen

- Fronius: [Hinweise GEN24 (Pionierkraft)](https://info.pionierkraft.de/hubfs/Dokumente%20PARTNER-Helpdesk/Vorab-Hinweise%20Fronius%20Verto%20_%20Tauro%20_%20GEN24%20_%20Primo%20GEN24%20_%20Symo%20GEN24.pdf), [Fronius und BYD über Modbus](https://libe.net/en/byd-modbus), [evcc SunSpec-Batterie Modell 124](https://docs.evcc.io/en/meters/sunspec-battery-model-124)
- SMA: [Tripower X ohne Zählerwerte (HA-Community)](https://community.home-assistant.io/t/support-for-more-sma-devices-e-g-tripower-x/713737), [Home Manager 2 über Modbus (evcc)](https://github.com/evcc-io/evcc/discussions/25480), [Tripower Smart Energy Modbus (Episensor)](https://episensor.com/devices/sma-sunny-tripower-smart-energy)
- SolarEdge: [solaredge_modbus](https://github.com/nmakel/solaredge_modbus), [Bekannte Probleme Batterie über Modbus](https://scriptagc.wasmer.app/https_github_com/WillCodeForCats/solaredge-modbus-multi/wiki/Known-Issues), [SunSpec Technical Note](https://knowledge-center.solaredge.com/sites/kc/files/sunspec-implementation-technical-note.pdf)
- Kostal: [TapHome Plenticore](https://taphome.com/en/compatibility/kostalplenticore/), [evcc Kostal Plenticore Hybrid](https://docs.evcc.io/en/meters/kostal-plenticore-hybrid)
- Huawei: [Verbindungswege und Ein-Client-Limit (HuaBook)](https://fop-50527c4b.mintlify.app/modbus/connection-methods), [Modbus-Cache-Proxy](https://github.com/cloudapp-dev/sun2000-modbus-cache), [huawei_solar](https://github.com/wlcrs/huawei_solar)
- GoodWe: [GoodWe Modbus TCP/IP Leitfaden 2021](https://community.goodwe.com/resources/goodwe/2021-11-2318335.pdf), [TapHome GoodWe ET Plus](https://taphome.com/en/compatibility/goodwe-et-plus-inverters/), [Reduxi Anleitung](https://support.reduxi.eu/hc/en-us/articles/34796835733265-Reduxi-with-GoodWe-Hybrid-Inverters), [goodwe-Bibliothek](https://github.com/marcelblijleven/goodwe)
- Sigenergy: [Sigenergy Modbus-Einstellungen](https://sigenergy.gitbook.io/sige-doc-en/mysigen-app-installer-manual/device-parameter-setup/sigenstor/modbus-settings), [Sigenergy Local Modbus](https://github.com/TypQxQ/Sigenergy-Local-Modbus), [openHAB-Binding](https://community.openhab.org/t/sigenergy-sigenstor-binding/170324)
