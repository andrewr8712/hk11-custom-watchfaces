# Custom watch faces for the HK11 PRO-A

This community guide documents the WF face format and Bluetooth Low Energy (BLE) install process tested on an **HK11 PRO-A**, firmware **3.07**, project **7678**. A Motorola Edge 60 Fusion running Android 17 successfully installed two original digital faces, Northline and Ember. Northline updated its live time. Ember is included as a test fixture. Other watches sold under similar names may require a different format or transfer protocol.

## Start with the example

The reference sender runs on an Android phone. A computer runs ADB over USB or Wi-Fi. The phone sends the file to the watch over BLE. The sender requires Android 13 or newer and was tested on Android 17. You need a phone already connected to the watch, the watch’s BLE address, and a computer with ADB, Python 3, JDK 21, Android SDK Platform 35, Android Build Tools 36.0.0, and the `zip` command. [Android’s ADB setup guide](https://developer.android.com/tools/adb)

1. Keep the watch near the phone and off its charger. In Wearfit Pro, connect to the watch and let syncing finish. Keep other watch apps idle and avoid starting another face transfer while the sender is running.
2. Get the watch’s **BLE** address from a local Bluetooth scan, for example with nRF Connect or BlueZ. Look for **HK11 PRO-A**. This is the address of the watch’s data interface. An entry with **Call** in its name is for audio. Stop scanning before transferring.
3. Build the sender with the bundled Ember file using the commands in [Building the Android sender](#building-the-android-sender). Use only face files built for your watch.
4. Set `PHONE_SERIAL` to the phone serial listed by `adb devices` and `WATCH_BLE_ADDRESS` to the watch’s address. From the directory containing `build/face.apk`, run:

   ```sh
   adb -s "$PHONE_SERIAL" install -g build/face.apk
   adb -s "$PHONE_SERIAL" shell am instrument -w -e watch "$WATCH_BLE_ADDRESS" \
       local.hk11.sessiontest/local.hk11.dialtest.DialTest
   adb -s "$PHONE_SERIAL" logcat -d -s HK11DialTest:I
   adb -s "$PHONE_SERIAL" uninstall local.hk11.sessiontest
   ```

5. Look for the loading animation, then the reddish **EMBER** face with amber digits. The sender logs the watch’s final file acknowledgement and its own success-status write. Check the watch display to confirm activation. Chooser art can differ from the installed design.

If the helper cannot discover the file service, verify the BLE address and reconnect through Wearfit. If the header or a data block times out, check that the watch is awake and off its charger and that Wearfit has finished syncing. Retry once after the connection has settled. If transfer completes and the face stays unchanged, verify that the sender implements the dial-start and dial-success messages described in the specification.

## Design a face

The reference builder supports a minimal digital layout. Supply a **320 × 385** RGBA PNG for the background and a **41 × 550** RGBA PNG containing ten **41 × 55** digit glyphs stacked from top to bottom in order **0–9**. Keep the area around the clock clear and draw the colon into the background. The watch fills in the current hours and minutes from the digit strip.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install 'Pillow>=10.1'
python hk11_dial.py build examples/ember_background.png examples/ember_digits.png my-face.bin
python hk11_dial.py extract my-face.bin build/decoded-face
```

Open `build/decoded-face/layout.json` and its PNGs to inspect the compiled assets. The included `examples/ember_preview.png` shows the intended face composition at a sample time. It represents the live face layout. The watch’s chooser thumbnail can use different artwork.

## Building the Android sender

Run these commands in the guide directory on a POSIX shell. Set `JAVA_HOME` and `ANDROID_HOME` for your local JDK and Android SDK. The packetizer uses Python’s standard library. It uses the input file’s basename as the name transmitted over BLE. Change the input path to install another compatible face.

```sh
export PATH="$JAVA_HOME/bin:$PATH"
bt="$ANDROID_HOME/build-tools/36.0.0"
platform="$ANDROID_HOME/platforms/android-35/android.jar"

mkdir -p build/classes build/dex
python3 packetize.py examples/ember.bin build/assets/transfer.dat
javac --release 17 -cp "$platform" -d build/classes android/src/DialTest.java
"$bt/d8" --min-api 33 --lib "$platform" --output build/dex \
    build/classes/local/hk11/dialtest/*.class
"$bt/aapt2" link -I "$platform" --manifest android/AndroidManifest.xml \
    -A build/assets -o build/unsigned.apk
(cd build/dex && zip -q ../unsigned.apk classes.dex)
"$bt/zipalign" -f 4 build/unsigned.apk build/aligned.apk

# Run keytool once. Keep your key and password private.
keytool -genkeypair -keystore build/local-test.p12 -alias local \
    -dname 'CN=HK11 Local Test' -keyalg RSA -keysize 2048 -validity 365
"$bt/apksigner" sign --ks build/local-test.p12 --out build/face.apk build/aligned.apk
"$bt/apksigner" verify build/face.apk
```

## Adding HK11 support to an existing project

A designer needs an **HK11 exporter** for this container and its tested layout records. A phone installer needs the **HK11 BLE transfer and install sequence**. A designer can export a file for the reference sender while native integration is under development.

| Project | Where the specification helps |
| --- | --- |
| UTE Custom Watchface Studio | It documents a related PA bitmap codec and already has a visual editor. An HK11 export profile would need its own WF container, canvas and layout records. [UTE Studio upstream](https://github.com/fayrnaldy/UTE-custom-watchface-studio) |
| Chronos | It already supports installation on some Wearfit-family watches. In our HK11 tests, Chronos stalled at 0% with both a stock file and Northline. The verified BLE sequence is a reference for model-specific installer support. [Chronos Wearfit face repository](https://github.com/fbiego/watch-face-wearfit) |
| Gadgetbridge | It has a Wearfit device family and watch-face installation infrastructure. An HK11 PRO-A contribution would need model detection and an installer verified on this firmware. Current device-specific compatibility remains unverified in this guide. [Gadgetbridge device catalog](https://gadgetbridge.org/gadgets/wearables/) |

Start by adding the digital-only export profile. Use Ember to check that the exporter produces a valid file and that the installer reaches the final acknowledgement. Then verify visible activation and a minute rollover on hardware.

[Read the WF file and BLE protocol specification](SPECIFICATION.md).
