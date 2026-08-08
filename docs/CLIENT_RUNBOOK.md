# SRTK — Client Runbook

You have hired a remote technician to unlock the bootloader of your
**Samsung Galaxy A32 4G (SM-A325F)** and install Magisk root. The technician
drives most of the work over a remote screen-sharing session. This sheet lists
the **only things you have to do with your hands**. Follow it when the
technician asks.

## What you are agreeing to (read before anything)

- Your phone will be **factory reset** (all data on the phone is erased).
- The **Knox fuse is tripped permanently**. This means:
  - Samsung Wallet / Samsung Pass / Secure Folder stop working.
  - The manufacturer **warranty is void**.
  - Some banking / payment apps may refuse to run on a rooted phone.
- You must **never take OTA software updates** on the rooted phone, and the
  bootloader must **never be relocked**.
- Back up your photos/WhatsApp/etc. **before** the unlock step.

## Before the session

1. Charge the phone to at least 70%.
2. Have your **data-capable USB cable** ready (a charging-only cable looks
   identical but won't work).
3. Know your **Google/Samsung account password** — you may need it after the
   wipe during the setup wizard.

## Your steps during the session

### Step A — Plug in + allow debugging (start)

1. Plug the phone into the PC with the USB cable.
2. On the phone, unlock the screen.
3. If a dialog **"Allow USB debugging?"** appears, tick **Always allow** and
   press **Allow / OK**.

### Step B — Enable developer mode (before unlock)

Only if the technician asks (the toolkit guides you on screen):

1. Open **Settings → About phone → Software information**.
2. Tap **Build number** seven (7) times until *"You are now a developer"*.
3. Open **Settings → Developer options**.
4. Switch **USB debugging** to ON.
5. Switch **OEM unlocking** to ON and confirm the warning dialog.

### Step C — The one physical button press (during unlock)

When the technician tells you the phone is in **Download Mode** (a blue/black
warning screen):

1. **Long-press Volume Up** until the screen changes.
2. When **"Unlock Bootloader?"** appears, **press Volume Up again** to confirm.

Your phone now wipes and reboots by itself. Do not touch it.

### Step D — Set up after the wipe (during unlock)

1. Complete the **setup wizard** — connect to your Wi-Fi and sign in.
2. Go to **Settings → About phone → Software information** and tap **Build
   number** seven (7) times again.
3. In **Developer options**, switch **USB debugging** back ON.
4. Accept the **"Allow USB debugging"** prompt if it appears.
5. Tell the technician *"done"* — they will verify the next step for you.

### Step E — During the patch and flash (minutes each)

- Keep the phone **awake and connected**. You don't need to touch it.
- **Do not unplug the cable** until the technician says the flash is finished.
- If you see a **red FAIL screen** during flashing, tell the technician
  immediately and do not unplug anything.

### Step F — When the technician asks for reboots

- Reboot the phone normally (Power → Restart) if asked, or just let the
  technician drive it from their software.

## After the session

- You are rooted. Keep the **final report** and the **evidence zip** the
  technician gives you — they record exactly what was done.
- If a banking/Google app ever complains about Play Integrity: your
  fingerprint expires roughly every 6 weeks. Contact the technician to refresh
  it (they can do it remotely).
- **Never** install system updates (OTA) yourself — bring the phone back to the
  technician instead.
- Keep the **EFS backup** safe — it can restore your IMEI if it ever goes
  missing.

## If something looks wrong

| You see | Do |
|---|---|
| Phone won't power on after a step | Hold Power ~10 s; tell the technician |
| Red FAIL in Odin during flash | Stop, do not unplug, call the technician |
| App says "device is rooted" and refuses | Expected — rooted phones do this; the technician can advise |
| Setup wizard after unlock | That is normal — the wipe did it. Follow Step D |
