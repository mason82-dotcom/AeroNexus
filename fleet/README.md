# AeroNexus Fleet

Geräteverwaltung: Modelle, Topologie (Fernsteuerung ↔ Fluggerät ↔ Nutzlast), Gerätewörterbuch.

- `sql/` – Ergänzungen zum Gerätewörterbuch (Matrice 4, RC Plus 2), werden beim ersten MySQL-Start eingespielt

Neues Gerät hinzufügen: Enums in api (DeviceTypeEnum, DeviceEnum, ggf. GatewayTypeEnum, PayloadModelEnum),
SQL-Zeile hier, Anzeigename in control (`src/types/device.ts`).
