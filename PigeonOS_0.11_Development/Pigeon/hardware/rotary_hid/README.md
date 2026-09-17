# Pigeon rotary encoder

Turns a rotary encoder into Left / Right / Space so Pigeon can navigate.

## Map

| Physical | Line / key | Effect |
|----------|------------|--------|
| CW | `RIGHT` | forward |
| CCW | `LEFT` | backward |
| PUSH | `PUSH` | activate |

## Arduino UNO Q (required steps)

UNO Q is **not** a normal USB-serial Arduino. `Serial` is UART on D0/D1. USB traffic uses **`Monitor`** through **Arduino_RouterBridge**, and the board’s Linux exposes that stream on **TCP port 7500**.

### Flash

1. Install **Arduino_RouterBridge** (Library Manager).
2. Board: **Arduino UNO Q**.
3. Upload `rotary_hid.ino` (must call `Bridge.begin()` then `Monitor.begin()`).
4. In IDE Serial Monitor, twist — you should see `RIGHT` / `LEFT` / `PUSH`.

### Pi host

1. Update Pigeon to **≥ 0.8.87**.
2. Plug UNO Q into the Pi with a **USB-C data cable**.
3. Install ADB on the Pi (once):

```bash
sudo apt-get install -y adb
# or: sudo apt-get install -y android-tools-adb
sudo usermod -aG plugdev "$USER"   # then re-login
```

4. Check:

```bash
adb devices
# should list the UNO Q as "device"

grep rotary ~/.pigeon_0_6/pigeon.log | tail -30
# want: rotary_serial: adb forward … and/or connected tcp 127.0.0.1:7500
# then when twisting: 'RIGHT' → forward (tcp)
```

5. Twist/click — Pigeon opens main settings and navigates.

### Env overrides

| Variable | Meaning |
|----------|---------|
| `PIGEON_ROTARY_TCP=127.0.0.1:7500` | Monitor TCP (default) |
| `PIGEON_ROTARY_TCP=0` | Disable TCP path |
| `PIGEON_ROTARY_PORT=/dev/ttyACM0` | Force USB CDC serial |
| `PIGEON_ROTARY_INVERT=1` | Swap CW/CCW |
| `PIGEON_ROTARY_SERIAL=0` | Disable bridge entirely |
| `PIGEON_ADB=/path/to/adb` | Custom adb binary |
| `PIGEON_ADB_SERIAL=<serial>` | Required when multiple authorized ADB devices are present; selects which device gets `adb forward tcp:7500` |

When more than one authorized device shows up in `adb devices`, Pigeon will not guess. Set `PIGEON_ADB_SERIAL` to the UNO Q serial from that list.

## HID boards (Leonardo / Pro Micro / Pico)

Upload the same sketch (HID mode). No `rotary_serial` / ADB needed — the board types real Left/Right/Space keys.

## Wiring

| Encoder | Pin |
|---------|-----|
| CLK / A | 2 |
| DT / B | 3 |
| SW | 4 |
| GND | GND |
| + | 5V or 3V3 |
