# This Python file uses the following encoding: utf-8

'''
Created on Jan 22, 2013

@author: feildmaster <www.feildmaster.com>
'''
import traceback
import socket as _socket
import ssl
import platform
import struct
import sys
import time as _time
import argparse
import threading
import cmd
try:
    import Mumble_pb2
except:
    print "WARNING: Module Mumble_pb2 not found.\nNow exiting.\n"
    sys.exit(0)

parser = None
connection = None
getMessageById = {}
# Messages in order of packet ID
getMessageId = {
    Mumble_pb2.Version:0,
    Mumble_pb2.UDPTunnel:1,
    Mumble_pb2.Authenticate:2,
    Mumble_pb2.Ping:3,
    Mumble_pb2.Reject:4,
    Mumble_pb2.ServerSync:5,
    Mumble_pb2.ChannelRemove:6,
    Mumble_pb2.ChannelState:7,
    Mumble_pb2.UserRemove:8,
    Mumble_pb2.UserState:9,
    Mumble_pb2.BanList:10,
    Mumble_pb2.TextMessage:11,
    Mumble_pb2.PermissionDenied:12,
    Mumble_pb2.ACL:13,
    Mumble_pb2.QueryUsers:14,
    Mumble_pb2.CryptSetup:15,
    Mumble_pb2.ContextActionModify:16,
    Mumble_pb2.ContextAction:17,
    Mumble_pb2.UserList:18,
    Mumble_pb2.VoiceTarget:19,
    Mumble_pb2.PermissionQuery:20,
    Mumble_pb2.CodecVersion:21,
    # The following may be wrong
    Mumble_pb2.UserStats:22,
    Mumble_pb2.ServerConfig:23,
    Mumble_pb2.SuggestConfig:24,
    Mumble_pb2.RequestBlob:25,
}

for message, id in getMessageId.items(): getMessageById[id] = message

class MumbleConnection(threading.Thread):
    session = None
    connected = False
    socket = None
    _callbackList = []
    nextPing = 0
    # Registers call back to type of callback
    def registerCallback(self, callback):
        if not callback in self._callbackList:
            self._callbackList.append(callback)
            return True
        return False
    # Return true if removed
    def unregisterCallback(self, callback):
        if callback in self._callbackList:
            self._callbackList.remove(callback)
            return True
        return False
    # Sends text message to the current channel
    def sendTextMessage(self, text):
        if not self.session: return False
        msg = Mumble_pb2.TextMessage()
        msg.session.append(self.session)
        if self.channel: msg.channel_id.append(self.channel)
        # Trim whitespace
        msg.message = text.strip()
        return self._sendMessage(msg)
    def connect(self, host=None, port=None, password=None):
        if password == None: password = self.password
        if host == None: host = self.server
        if port == None: port = self.port
        if not self.socket and not self.connected:
            socket = ssl.wrap_socket(_socket.socket(type=_socket.SOCK_STREAM), ssl_version=ssl.PROTOCOL_TLSv1)
            socket.setsockopt(_socket.SOL_TCP, _socket.TCP_NODELAY, 1)
            try:
                socket.connect((host, port))
            except:
                print "Could not connect to %s:%s" % (host, port)
                return False
            ver = Mumble_pb2.Version()
            ver.release = "1.2.4-beta1-33-ge9ce44a"
            ver.version = 66048
            ver.os = platform.system()
            ver.os_version = "mumblebot"

            auth = Mumble_pb2.Authenticate()
            auth.username = self.nickname
            if password: auth.password = password
            auth.celt_versions.append(-2147483637)
            auth.opus = True
            
            self.socket = socket
            if self._sendMessages(ver, auth): self.connected = True
            else:
                self.socket = None
                self.connected = False
            return self.connected
        return False
    def disconnect(self,exit=False):
        if self.connected:
            self.socket.close()
            self.socket = None
            self.connected = False
            return True
        return False
    def joinChannel(self,channel=None):
        if channel == None: channel = self.channel
        state = Mumble_pb2.UserState()
        state.session = self.session
        if channel: state.channel_id = channel
        return self._sendMessage(state)
    def _readPacket(self):
        meta = self._readNext(6)
        if meta:
            msgType, length = struct.unpack(">HI", meta)
            stringMessage = self._readNext(length)
            if not msgType in self._getMessageById:
                if self.verbose: print msgType, "Unknown message type"
                return
            messageClass = self._getMessageById[msgType]
            message = messageClass()
            message.ParseFromString(stringMessage)
            if not msgType in [1, 3] and self.verbose: print "Message of type " + messageClass.__name__ + " ("+str(msgType)+") received!\n" + message
            if not self.session and messageClass == Mumble_pb2.ServerSync:
                self.session = message.session
                self._joinChannel()
            elif messageClass == Mumble_pb2.ChannelState:
                if message.name == self.channel: self.channel = message.channel_id
            elif self.session and messageClass == Mumble_pb2.UserRemove:
                if message.session == self.session: self.disconnect()
            else:
                for call in self._textCallbacks:
                    if call.shouldHandleMessage(msgType, message) and call.handleMessage(msgType, message): break
    def _sendMessages(self, *messages):
        ret = True
        for message in messages: ret &= self._sendMessage(message)
        return ret;
    def _sendMessage(self, message):
        packet = struct.pack(">HI", self._getMessageId[type(message)], message.ByteSize()) + message.SerializeToString()
        return self._sendTotally(packet)
    # Sends to the socket, but does it throw errors?
    def _sendTotally(self, message):
        while len(message) > 0:
            sent = self.socket.send(message)
            if sent < 0: return False
            message = message[sent:]
        return True
    # Reads from the socket, but does it throw errors?
    def _readNext(self, size):
        message = ""
        while len(message) < size:
            received = self.socket.recv(size - len(message))
            message += received
            if len(received) == 0: return None
        return message
    # Forces a ping send, resets the nextPing time
    def _sendPing(self,time=_time.time()):
        ping = Mumble_pb2.Ping()
        ping.timestamp = (self._pingTotal * 5000000)
        ping.good = 0
        ping.late = 0
        ping.lost = 0
        ping.resync = 0
        ping.udp_packets = 0
        ping.tcp_packets = self._pingTotal
        ping.udp_ping_avg = 0
        ping.udp_ping_var = 0.0
        ping.tcp_ping_avg = 50
        ping.tcp_ping_var = 50
        self._pingTotal += 1
        self._sendMessage(ping)
        self.nextPing = time + 5
    def run(self):
        while self.isAlive():
            try:
                # Keep running even if we're not connected
                if not self.socket:
                    continue
                # Add the ping to queue
                time = _time.time()
                if time > self.nextPing:
                    try:
                        self._sendPing(time)
                    except:
                        traceback.format_exc()
                # Read a packet (should perform more than 1 at a time?)
                self._readPacket()
                # TODO: Send the queue (everything is instant at the moment)
            except:
                traceback.format_exc()
class Callback():
    def __init__(self, name):
        self.name = name
    def shouldHandleMessage(self, msgType, message):
        ''' Return True if you want to attempt handling the message '''
        return False
    def handleMessage(self, msgType, message):
        ''' Return True if this message was handled '''
        return False
class MumbleCommand(cmd.Cmd):
    def __init__(self, connection):
        cmd.Cmd.__init__(self)
        self.connection = connection
        self.prompt = '> '
    def log(self, line):
        self.stdout.write(line + '\n')
    def do_connect(self, line):
        args = line.split[' ']
        host = None
        port = None
        # The host *has* to be first
        if len(args) > 1 and isInt(args[1]):
            port = args[1]
        if len(args) > 0:
            if isInt(args[0]):
                port = args[0]
            else:
                host = args[0]
        self.connection.connect(host=host, port=port)
    def do_disconnect(self, line):
        ''' Disconnects from the server '''
        if self.connection.disconnect():
            self.log('Disconnected')
        else:
            self.log('Already disconnected')
    def do_say(self, line):
        ''' Sends message to the current channel '''
        if line != '': self.connection.sendTextMessage(line)
    def do_debug(self, line):
        ''' Toggles/Sets the bots verbose mode '''
        if line != '':
            value = line.tolower() == 'true'
        else:
            value = not self.connection.verbose
        self.connection.verbose = value
        self.log('Debug: %s' % value)
    def do_exit(self, line):
        ''' Stops the bot '''
        self.do_say(line)
        return True
    def do_stop(self, line):
        ''' Alias for 'exit' '''
        return self.do_exit(line)
    def do_EOF(self, line):
        ''' Called when the terminal fails, processes as 'exit' '''
        return self.do_exit(line)
    def do_rename(self, line):
        ''' Renames the bot, setting the name upon the next connection '''
        if line == '':
            self.log('Current nick: %s' % self.connection.nick)
            return
        if self.connection.connected:
            self.log('You must reconnect to apply the name change')
        nick = line.split(' ')[0]
        self.connection.nick = nick
        self.log('Now known as: %s' % nick)
    def emptyline(self):
        # We don't want to repeat the last command, that's silly
        return
def isInt(string):
    try:
        int(string)
        return True
    except:
        return False
def main():
    parser = argparse.ArgumentParser(description="",
            version="%prog 1.0",
            usage="\t%prog --channel \"Root\" --server \"localhost\"",
        )
    
    parser.add_argument("-s","--server",help="",action="store",type=str,default="localhost")
    parser.add_argument("-p","--port",help="",action="store",type=int,default=64738)
    parser.add_argument("-n","--nick",help="",action="store",type=str,default="mumblebot")
    parser.add_argument("-c","--channel",help="",action="store",type=str,default="Root")
    parser.add_argument("--verbose",help="",action="store_true")
    parser.add_argument("--password",help="",action="store",type=str)
    
    connection = MumbleConnection()
    parser.parse_known_args(namespace=connection)
    connection.setDaemon(True)
    connection.connect(host = connection.server, port = connection.port):
    connection.start()
    
    command = MumbleCommand(connection)
    command.cmdloop()
if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        # Silence the interrupt
        pass
