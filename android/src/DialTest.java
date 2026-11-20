package local.hk11.dialtest;

import android.app.Activity;
import android.app.Instrumentation;
import android.bluetooth.*;
import android.content.Context;
import android.os.Bundle;
import android.util.Log;

import java.io.DataInputStream;
import java.io.IOException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;

public final class DialTest extends Instrumentation {
    private static final String TAG = "HK11DialTest";
    private static final UUID CONTROL = uuid("0002");
    private static final UUID FILE_SERVICE = uuid("0011");
    private static final UUID FILE_WRITE = uuid("0012");
    private static final UUID FILE_NOTIFY = uuid("0013");
    private static final UUID CCC = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb");
    private static final byte[] SPEED_ON = {(byte)0xab, 0, 4, (byte)0xff, (byte)0x87, (byte)0x80, 1};
    private static final byte[] SPEED_OFF = {(byte)0xab, 0, 4, (byte)0xff, (byte)0x87, (byte)0x80, 0};
    private final LinkedBlockingQueue<Event> events = new LinkedBlockingQueue<>();
    private final LinkedBlockingQueue<byte[]> notifications = new LinkedBlockingQueue<>();
    private static final long PACKET_INTERVAL = TimeUnit.MILLISECONDS.toNanos(17);
    private final List<Long> packetIntervals = new ArrayList<>();
    private String address;

    @Override public void onCreate(Bundle args) {
        address = args.getString("watch");
        start();
    }

    @Override public void onStart() {
        Bundle result = new Bundle();
        try {
            transfer();
            reportPacketIntervals();
            result.putString("result", "Watch confirmed a completed dial transfer");
            finish(Activity.RESULT_OK, result);
        } catch (Exception error) {
            reportPacketIntervals();
            report("Stopped: " + error.getMessage());
            result.putString("result", error.getMessage());
            finish(Activity.RESULT_CANCELED, result);
        }
    }

    private void transfer() throws Exception {
        Context context = getTargetContext();
        BluetoothAdapter adapter = context.getSystemService(BluetoothManager.class).getAdapter();
        require(adapter != null && adapter.isEnabled(), "Phone Bluetooth is off");
        require(address != null && BluetoothAdapter.checkBluetoothAddress(address), "Invalid target");
        BluetoothDevice device = adapter.getRemoteDevice(address);
        try (DataInputStream packets = new DataInputStream(context.getAssets().open("transfer.dat"))) {
            BluetoothGatt gatt = device.connectGatt(context, false, callback, BluetoothDevice.TRANSPORT_LE);
            require(gatt != null, "Android could not start GATT");
            try {
                await("connect", 18);
                report("Connected over phone Bluetooth");
                require(gatt.requestMtu(247), "Android declined MTU request");
                require(await("MTU", 8).value >= 244, "Negotiated MTU too small for the known packets");
                require(gatt.discoverServices(), "Android declined service discovery");
                await("services", 12);
                BluetoothGattService service = gatt.getService(FILE_SERVICE);
                require(service != null, "Watch did not expose its file service");
                BluetoothGattCharacteristic write = service.getCharacteristic(FILE_WRITE);
                BluetoothGattCharacteristic notify = service.getCharacteristic(FILE_NOTIFY);
                BluetoothGattCharacteristic control = findCharacteristic(gatt, CONTROL);
                require(write != null && notify != null && control != null,
                        "Watch did not expose the necessary characteristics");
                BluetoothGattDescriptor descriptor = notify.getDescriptor(CCC);
                require(descriptor != null, "Watch did not expose the notification descriptor");
                require(gatt.setCharacteristicNotification(notify, true), "Android declined notifications");
                int subscribed = gatt.writeDescriptor(descriptor, BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE);
                require(subscribed == BluetoothStatusCodes.SUCCESS, "Android declined subscription: " + subscribed);
                await("subscribe", 6);

                write(gatt, control, SPEED_ON);
                try {
                    Thread.sleep(150);
                    writeDialState(gatt, control, 0, "start");
                    try {
                        send(gatt, write, packets);
                    } catch (Exception error) {
                        try {
                            writeDialState(gatt, control, 2, "failure");
                        } catch (Exception statusError) {
                            error.addSuppressed(statusError);
                            report("Unable to send dial failure status: " + statusError.getMessage());
                        }
                        throw error;
                    }
                    Thread.sleep(35);
                    writeDialState(gatt, control, 1, "success");
                    Thread.sleep(110);
                } finally {
                    try {
                        write(gatt, control, SPEED_OFF);
                        Thread.sleep(150);
                    } catch (IOException error) {
                        report("Unable to restore watch transfer mode: " + error.getMessage());
                    }
                }
            } finally {
                gatt.disconnect();
                gatt.close();
            }
        }
    }

    private void writeDialState(BluetoothGatt gatt, BluetoothGattCharacteristic control,
                                int state, String name) throws Exception {
        byte[] frame = new byte[12];
        frame[0] = (byte) 0xb0;
        frame[11] = (byte) state;
        write(gatt, control, frame);
        report("Sent watch dial " + name + " status");
    }

    private void send(BluetoothGatt gatt, BluetoothGattCharacteristic write, DataInputStream packets)
            throws Exception {
        write(gatt, write, readPacket(packets));
        require(reply(0xb0).status == 0, "Watch rejected the file header");
        report("Watch accepted the file header");
        int totalBlocks = packets.readUnsignedShort();
        for (int block = 0; block < totalBlocks; block++) {
            int packetCount = packets.readUnsignedShort();
            long previousWrite = 0;
            for (int packet = 0; packet < packetCount; packet++) {
                byte[] data = readPacket(packets);
                if (packet != 0) {
                    waitUntil(previousWrite + PACKET_INTERVAL);
                }
                long now = System.nanoTime();
                if (packet != 0) packetIntervals.add(now - previousWrite);
                previousWrite = now;
                write(gatt, write, data);
            }
            Reply acknowledgement = reply(0xb1);
            if (block == totalBlocks - 1 && acknowledgement.isComplete()) {
                report("Watch confirmed transfer after the final block");
                return;
            }
            require(acknowledgement.index == block && acknowledgement.status == 0,
                    "Unexpected block acknowledgement: " + acknowledgement.index + ", " + acknowledgement.status);
            report("Watch acknowledged block " + (block + 1) + "/" + totalBlocks);
        }
        require(reply(0xb1).isComplete(), "Watch did not confirm a completed transfer");
        report("Watch confirmed transfer after all blocks");
    }

    private static void waitUntil(long deadline) throws InterruptedException {
        long remaining;
        while ((remaining = deadline - System.nanoTime()) > 0) {
            TimeUnit.NANOSECONDS.sleep(remaining);
        }
    }

    private void reportPacketIntervals() {
        if (packetIntervals.isEmpty()) return;
        packetIntervals.sort(null);
        long median = packetIntervals.get(packetIntervals.size() / 2);
        report("Median Android write-call interval " + TimeUnit.NANOSECONDS.toMillis(median)
                + " ms across " + packetIntervals.size() + " samples");
    }

    private byte[] readPacket(DataInputStream packets) throws IOException {
        byte[] packet = new byte[packets.readUnsignedShort()];
        packets.readFully(packet);
        return packet;
    }

    private BluetoothGattCharacteristic findCharacteristic(BluetoothGatt gatt, UUID uuid) {
        for (BluetoothGattService service : gatt.getServices()) {
            BluetoothGattCharacteristic characteristic = service.getCharacteristic(uuid);
            if (characteristic != null) return characteristic;
        }
        return null;
    }

    private void write(BluetoothGatt gatt, BluetoothGattCharacteristic characteristic, byte[] packet)
            throws Exception {
        int status = gatt.writeCharacteristic(characteristic, packet,
                BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE);
        require(status == BluetoothStatusCodes.SUCCESS, "Android declined GATT write: " + status);
        await("write", 5);
    }

    private Event await(String action, long seconds) throws Exception {
        Event event = events.poll(seconds, TimeUnit.SECONDS);
        require(event != null, "Timed out waiting for " + action);
        require(action.equals(event.name) && event.status == BluetoothGatt.GATT_SUCCESS,
                action + " failed during " + event.name + " with status " + event.status);
        return event;
    }

    private Reply reply(int opcode) throws Exception {
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(12);
        while (true) {
            byte[] packet = notifications.poll(Math.max(0, deadline - System.nanoTime()), TimeUnit.NANOSECONDS);
            require(packet != null, String.format("Timed out waiting for watch acknowledgement 0x%02x", opcode));
            if (packet.length >= 4 && (packet[0] & 255) == opcode) {
                return new Reply(((packet[1] & 255) << 8) | (packet[2] & 255), packet[3] & 255);
            }
        }
    }

    private static void require(boolean condition, String message) throws IOException {
        if (!condition) throw new IOException(message);
    }

    private static void report(String message) {
        Log.i(TAG, message);
    }

    private static UUID uuid(String suffix) {
        return UUID.fromString("6e40" + suffix + "-b5a3-f393-e0a9-e50e24dcca9e");
    }

    private record Event(String name, int status, int value) {}
    private record Reply(int index, int status) {
        boolean isComplete() { return index == 0xffff && status == 3; }
    }

    private final BluetoothGattCallback callback = new BluetoothGattCallback() {
        @Override public void onConnectionStateChange(BluetoothGatt gatt, int status, int newState) {
            String event = newState == BluetoothProfile.STATE_CONNECTED ? "connect" : "disconnect";
            events.add(new Event(event, status, newState));
        }
        @Override public void onMtuChanged(BluetoothGatt gatt, int mtu, int status) {
            events.add(new Event("MTU", status, mtu));
        }
        @Override public void onServicesDiscovered(BluetoothGatt gatt, int status) {
            events.add(new Event("services", status, 0));
        }
        @Override public void onDescriptorWrite(BluetoothGatt gatt, BluetoothGattDescriptor descriptor, int status) {
            events.add(new Event("subscribe", status, 0));
        }
        @Override public void onCharacteristicWrite(BluetoothGatt gatt, BluetoothGattCharacteristic ch, int status) {
            events.add(new Event("write", status, 0));
        }
        @Override public void onCharacteristicChanged(BluetoothGatt gatt, BluetoothGattCharacteristic ch, byte[] value) {
            if (FILE_NOTIFY.equals(ch.getUuid())) notifications.add(Arrays.copyOf(value, value.length));
        }
    };
}
