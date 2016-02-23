####  Authentication parameters
from socket import *
from struct import *
import scapy
import sys
from nose.tools import assert_equal, assert_not_equal, assert_raises, assert_true

USER = "raduser"
PASS = "radpass"
WRONG_USER = "XXXX"
WRONG_PASS = "XXXX"
NO_USER = ""
NO_PASS = ""
DEV = "tap0"
ETHERTYPE_PAE = 0x888e
PAE_GROUP_ADDR = "\xff\xff\xff\xff\xff\xff"
EAPOL_VERSION = 1
EAPOL_EAPPACKET = 0
EAPOL_START = 1
EAPOL_LOGOFF = 2
EAPOL_KEY = 3
EAPOL_ASF = 4
EAP_REQUEST = 1
EAP_RESPONSE = 2
EAP_SUCCESS = 3
EAP_FAILURE = 4
EAP_TYPE_ID = 1
EAP_TYPE_MD5 = 4
EAP_TYPE_MSCHAP = 26
EAP_TYPE_TLS = 13
cCertMsg = '\x0b\x00\x00\x03\x00\x00\x00'
TLS_LENGTH_INCLUDED = 0x80

def ethernet_header(src, dst, req_type):
    return dst+src+pack("!H", req_type)

class EapolPacket(object):
    
    def __init__(self, intf = 'veth0'):
        self.intf = intf
        self.s = None
        self.max_payload_size = 1600

    def setup(self):
        self.s = socket(AF_PACKET, SOCK_RAW, htons(ETHERTYPE_PAE))
        self.s.bind((self.intf, ETHERTYPE_PAE))
        self.mymac = self.s.getsockname()[4]
        self.llheader = ethernet_header(self.mymac, PAE_GROUP_ADDR, ETHERTYPE_PAE)

    def cleanup(self):
        if self.s is not None:
            self.s.close()
            self.s = None
            
    def eapol(self, req_type, payload=""):
        return pack("!BBH", EAPOL_VERSION, req_type, len(payload))+payload

    def eap(self, code, pkt_id, req_type=0, data=""):
        if code in [EAP_SUCCESS, EAP_FAILURE]:
            return pack("!BBH", code, pkt_id, 4)
        else:
            return pack("!BBHB", code, pkt_id, 5+len(data), req_type)+data

    def eapTLS(self, code, pkt_id, flags = TLS_LENGTH_INCLUDED, data=""):
        req_type = EAP_TYPE_TLS
        if code in [EAP_SUCCESS, EAP_FAILURE]:
            return pack("!BBH", code, pkt_id, 4)
        else:
            if flags & TLS_LENGTH_INCLUDED:
                flags_dlen = pack("!BL", flags, len(data))
                return pack("!BBHB", code, pkt_id, 5+len(flags_dlen)+len(data), req_type) + flags_dlen + data
            flags_str = pack("!B", flags)
            return pack("!BBHB", code, pkt_id, 5+len(flags_str)+len(data), req_type) + flags_str + data

    def eapol_send(self, eapol_type, eap_payload):
        return self.s.send(self.llheader + self.eapol(eapol_type, eap_payload))

    def eapol_recv(self):
        p = self.s.recv(self.max_payload_size)[14:]
        vers,pkt_type,eapollen  = unpack("!BBH",p[:4])
        print "Version %d, type %d, len %d" %(vers, pkt_type, eapollen)
        assert_equal(pkt_type, EAPOL_EAPPACKET)
        return p[4:]

    def eapol_start(self):
        eap_payload = self.eap(EAPOL_START, 2)
        return self.eapol_send(EAPOL_START, eap_payload)

    def eapol_id_req(self, pkt_id = 0, user = USER):
        eap_payload = self.eap(EAP_RESPONSE, pkt_id, EAP_TYPE_ID, user)
        return self.eapol_send(EAPOL_EAPPACKET, eap_payload)
