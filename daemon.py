#!/opt/i2c2mqtt/bin/python

from contextlib import contextmanager
from itertools import chain
import logging
from sys import stdout

from time import sleep
from mcp23017 import MCP23017, GPPUA, GPPUB
from paho.mqtt.client import Client as MqttClient
from smbus import SMBus

logging.basicConfig(stream=stdout, level=logging.INFO)
log = logging.getLogger("daemon")

mcp23017_bus = 1
mcp23017_addrs = [0x20, 0x21]

mqtt_broker = "openhab"
mqtt_port = 1883
mqtt_topic = "Apartment/Window/Alarm"
mqtt_client_id = mqtt_topic.replace("/", "-")
mqtt_will_topic = f"tele/{mqtt_topic}/LWT"

FIRST_RECONNECT_DELAY = 1
RECONNECT_RATE = 2
MAX_RECONNECT_COUNT = 12
MAX_RECONNECT_DELAY = 60

def connect_mcp23017(addr, smbus):
    mcp = MCP23017(addr, smbus)
    mcp.set_all_input()
    mcp.i2c.write_to(mcp.address, GPPUA, 0xFF)
    mcp.i2c.write_to(mcp.address, GPPUB, 0xFF)
    return mcp


def on_connect(client: MqttClient, userdata, flags, rc):
    log.info("on_connect called")
    if rc == 0 and client.is_connected():
        log.info("Connected to MQTT broker")
        client.publish(mqtt_will_topic, "Online", retain=True)
    else:
        log.error("Failed to connect, return code %d", rc)


def on_disconnect(client: MqttClient, userdata, rc):
    log.error("Disconnected with result code: %s", rc)
    reconnect_count, reconnect_delay = 0, FIRST_RECONNECT_DELAY
    while reconnect_count < MAX_RECONNECT_COUNT:
        log.info("Reconnecting in %d seconds...", reconnect_delay)
        sleep(reconnect_delay)

        try:
            client.reconnect()
            log.info("Reconnected successfully!")
            return
        except Exception as err:
            log.error("%s. Reconnect failed. Retrying...", err)

        reconnect_delay *= RECONNECT_RATE
        reconnect_delay = min(reconnect_delay, MAX_RECONNECT_DELAY)
        reconnect_count += 1
    log.info("Reconnect failed after %s attempts. Exiting...", reconnect_count)
    exit(1)


@contextmanager
def connect_mqtt():
    log.info("Connecting to MQTT broker %s:%d", mqtt_broker, mqtt_port)
    client = MqttClient(mqtt_client_id)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.will_set(mqtt_will_topic, "Offline", retain=True)
    client.connect(mqtt_broker, mqtt_port)
    client.loop_start()
    try:
        yield client
    finally:
        client.loop_stop()


smbus = SMBus(mcp23017_bus)
mcps = list(map(lambda it: connect_mcp23017(it, smbus), mcp23017_addrs))
with connect_mqtt() as client:
    last_values = []
    while True:
        values = list(chain.from_iterable(map(MCP23017.digital_read_all, mcps)))
        for byte_index, value in enumerate(values):
            for bit_index in range(0, 8):
                bit = bool(value & (1 << bit_index))
                last_bit = bool(last_values[byte_index] & (1 << bit_index)) if last_values else None
                if bit != last_bit:
                    index = byte_index * 8 + bit_index + 1
                    client.publish(f"stat/{mqtt_topic}/CONTACT{index}", "OPEN" if bit else "CLOSED")
        last_values = values
        sleep(0.1)
