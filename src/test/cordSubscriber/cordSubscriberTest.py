import unittest
from nose.tools import *
from nose.twistedtools import reactor, deferred
from twisted.internet import defer
from scapy.all import *
import time, monotonic
import os, sys
import tempfile
import random
import threading
import json
import requests
from Stats import Stats
from OnosCtrl import OnosCtrl
from DHCP import DHCPTest
from EapTLS import TLSAuthTest
from Channels import Channels, IgmpChannel
from subscriberDb import SubscriberDB
from threadPool import ThreadPool
from portmaps import g_subscriber_port_map
from OltConfig import *
from CordTestServer import cord_test_onos_restart

log.setLevel('INFO')

class Subscriber(Channels):
      PORT_TX_DEFAULT = 2
      PORT_RX_DEFAULT = 1
      INTF_TX_DEFAULT = 'veth2'
      INTF_RX_DEFAULT = 'veth0'
      STATS_RX = 0
      STATS_TX = 1
      STATS_JOIN = 2
      STATS_LEAVE = 3
      SUBSCRIBER_SERVICES = 'DHCP IGMP TLS'
      def __init__(self, name = 'sub', service = SUBSCRIBER_SERVICES, port_map = None,
                   num = 1, channel_start = 0,
                   tx_port = PORT_TX_DEFAULT, rx_port = PORT_RX_DEFAULT,
                   iface = INTF_RX_DEFAULT, iface_mcast = INTF_TX_DEFAULT,
                   mcast_cb = None, loginType = 'wireless'):
            self.tx_port = tx_port
            self.rx_port = rx_port
            self.port_map = port_map or g_subscriber_port_map
            try:
                  self.tx_intf = self.port_map[tx_port]
                  self.rx_intf = self.port_map[rx_port]
            except:
                  self.tx_intf = self.port_map[self.PORT_TX_DEFAULT]
                  self.rx_intf = self.port_map[self.PORT_RX_DEFAULT]

            log.info('Subscriber %s, rx interface %s, uplink interface %s' %(name, self.rx_intf, self.tx_intf))
            Channels.__init__(self, num, channel_start = channel_start,
                              iface = self.rx_intf, iface_mcast = self.tx_intf, mcast_cb = mcast_cb)
            self.name = name
            self.service = service
            self.service_map = {}
            services = self.service.strip().split(' ')
            for s in services:
                  self.service_map[s] = True
            self.loginType = loginType
            ##start streaming channels
            self.join_map = {}
            ##accumulated join recv stats
            self.join_rx_stats = Stats()
            self.recv_timeout = False

      def has_service(self, service):
            if self.service_map.has_key(service):
                  return self.service_map[service]
            if self.service_map.has_key(service.upper()):
                  return self.service_map[service.upper()]
            return False

      def channel_join_update(self, chan, join_time):
            self.join_map[chan] = ( Stats(), Stats(), Stats(), Stats() )
            self.channel_update(chan, self.STATS_JOIN, 1, t = join_time)

      def channel_join(self, chan = 0, delay = 2):
            '''Join a channel and create a send/recv stats map'''
            if self.join_map.has_key(chan):
                  del self.join_map[chan]
            self.delay = delay
            chan, join_time = self.join(chan)
            self.channel_join_update(chan, join_time)
            return chan

      def channel_join_next(self, delay = 2):
            '''Joins the next channel leaving the last channel'''
            if self.last_chan:
                  if self.join_map.has_key(self.last_chan):
                        del self.join_map[self.last_chan]
            self.delay = delay
            chan, join_time = self.join_next()
            self.channel_join_update(chan, join_time)
            return chan

      def channel_jump(self, delay = 2):
            '''Jumps randomly to the next channel leaving the last channel'''
            if self.last_chan is not None:
                  if self.join_map.has_key(self.last_chan):
                        del self.join_map[self.last_chan]
            self.delay = delay
            chan, join_time = self.jump()
            self.channel_join_update(chan, join_time)
            return chan

      def channel_leave(self, chan = 0):
            if self.join_map.has_key(chan):
                  del self.join_map[chan]
            self.leave(chan)

      def channel_update(self, chan, stats_type, packets, t=0):
            if type(chan) == type(0):
                  chan_list = (chan,)
            else:
                  chan_list = chan
            for c in chan_list:
                  if self.join_map.has_key(c):
                        self.join_map[c][stats_type].update(packets = packets, t = t)

      def channel_receive(self, chan, cb = None, count = 1, timeout = 5):
            log.info('Subscriber %s on port %s receiving from group %s, channel %d' %
                     (self.name, self.rx_intf, self.gaddr(chan), chan))
            r = self.recv(chan, cb = cb, count = count, timeout = timeout)
            if self.recv_timeout:
                  ##Negative test case is disabled for now
                  assert_equal(len(r), 0)

      def recv_channel_cb(self, pkt):
            ##First verify that we have received the packet for the joined instance
            log.info('Packet received for group %s, subscriber %s, port %s' %
                     (pkt[IP].dst, self.name, self.rx_intf))
            if self.recv_timeout:
                  return
            chan = self.caddr(pkt[IP].dst)
            assert_equal(chan in self.join_map.keys(), True)
            recv_time = monotonic.monotonic() * 1000000
            join_time = self.join_map[chan][self.STATS_JOIN].start
            delta = recv_time - join_time
            self.join_rx_stats.update(packets=1, t = delta, usecs = True)
            self.channel_update(chan, self.STATS_RX, 1, t = delta)
            log.debug('Packet received in %.3f usecs for group %s after join' %(delta, pkt[IP].dst))

class subscriber_pool:

      def __init__(self, subscriber, test_cbs):
            self.subscriber = subscriber
            self.test_cbs = test_cbs

      def pool_cb(self):
            for cb in self.test_cbs:
                  if cb:
                        cb(self.subscriber)

class subscriber_exchange(unittest.TestCase):

      apps = ('org.opencord.aaa', 'org.onosproject.dhcp')
      olt_apps = () #'org.opencord.cordmcast')
      table_app = 'org.ciena.cordigmp'
      dhcp_server_config = {
        "ip": "10.1.11.50",
        "mac": "ca:fe:ca:fe:ca:fe",
        "subnet": "255.255.252.0",
        "broadcast": "10.1.11.255",
        "router": "10.1.8.1",
        "domain": "8.8.8.8",
        "ttl": "63",
        "delay": "2",
        "startip": "10.1.11.51",
        "endip": "10.1.11.100"
      }

      aaa_loaded = False
      test_path = os.path.dirname(os.path.realpath(__file__))
      table_app_file = os.path.join(test_path, '..', 'apps/ciena-cordigmp-multitable-2.0-SNAPSHOT.oar')
      app_file = os.path.join(test_path, '..', 'apps/ciena-cordigmp-2.0-SNAPSHOT.oar')
      onos_config_path = os.path.join(test_path, '..', 'setup/onos-config')
      olt_conf_file = os.path.join(test_path, '..', 'setup/olt_config.json')
      cpqd_path = os.path.join(test_path, '..', 'setup')
      ovs_path = cpqd_path
      test_services = ('IGMP', 'TRAFFIC')
      num_joins = 0
      num_subscribers = 0
      num_channels = 0
      recv_timeout = False

      @classmethod
      def load_device_id(cls):
            '''Configure the device id'''
            did = OnosCtrl.get_device_id()
            #Set the default config
            cls.device_id = did
            cls.device_dict = { "devices" : {
                        "{}".format(did) : {
                              "basic" : {
                                    "driver" : "pmc-olt"
                                    }
                              }
                        },
                  }
            return did

      @classmethod
      def setUpClass(cls):
          '''Load the OLT config and activate relevant apps'''
          did = cls.load_device_id()
          network_cfg = { "devices" : {
                  "{}".format(did) : {
                        "basic" : {
                              "driver" : "pmc-olt"
                              }
                        }
                  },
          }
          ## Restart ONOS with cpqd driver config for OVS
          cls.start_onos(network_cfg = network_cfg)
          cls.install_app_table()
          cls.olt = OltConfig(olt_conf_file = cls.olt_conf_file)
          OnosCtrl.cord_olt_config(cls.olt.olt_device_data())
          cls.port_map, cls.port_list = cls.olt.olt_port_map()
          cls.activate_apps(cls.apps + cls.olt_apps)

      @classmethod
      def tearDownClass(cls):
          '''Deactivate the olt apps and restart OVS back'''
          apps = cls.olt_apps + ( cls.table_app,)
          for app in apps:
              onos_ctrl = OnosCtrl(app)
              onos_ctrl.deactivate()
          cls.uninstall_app_table()
          cls.start_onos(network_cfg = {})

      @classmethod
      def activate_apps(cls, apps):
            for app in apps:
                  onos_ctrl = OnosCtrl(app)
                  status, _ = onos_ctrl.activate()
                  assert_equal(status, True)
                  time.sleep(2)

      @classmethod
      def install_app_table(cls):
            ##Uninstall the existing app if any
            OnosCtrl.uninstall_app(cls.table_app)
            time.sleep(2)
            log.info('Installing the multi table app %s for subscriber test' %(cls.table_app_file))
            OnosCtrl.install_app(cls.table_app_file)
            time.sleep(3)

      @classmethod
      def uninstall_app_table(cls):
            ##Uninstall the table app on class exit
            OnosCtrl.uninstall_app(cls.table_app)
            time.sleep(2)
            log.info('Installing back the cord igmp app %s for subscriber test on exit' %(cls.app_file))
            OnosCtrl.install_app(cls.app_file)

      @classmethod
      def start_onos(cls, network_cfg = None):
            v = bool(int(os.getenv('ONOS_RESTART_DISABLED', 0)))
            if v:
                  log.info('ONOS restart is disabled. Skipping ONOS restart')
                  return
            if network_cfg is None:
                  network_cfg = cls.device_dict

            if type(network_cfg) is tuple:
                  res = []
                  for v in network_cfg:
                        res += v.items()
                  config = dict(res)
            else:
                  config = network_cfg
            log.info('Restarting ONOS with new network configuration')
            return cord_test_onos_restart(config = config)

      @classmethod
      def remove_onos_config(cls):
            try:
                  os.unlink('{}/network-cfg.json'.format(cls.onos_config_path))
            except: pass

      @classmethod
      def start_cpqd(cls, mac = '00:11:22:33:44:55'):
            dpid = mac.replace(':', '')
            cpqd_file = os.sep.join( (cls.cpqd_path, 'cpqd.sh') )
            cpqd_cmd = '{} {}'.format(cpqd_file, dpid)
            ret = os.system(cpqd_cmd)
            assert_equal(ret, 0)
            time.sleep(10)
            device_id = 'of:{}{}'.format('0'*4, dpid)
            return device_id

      @classmethod
      def start_ovs(cls):
            ovs_file = os.sep.join( (cls.ovs_path, 'of-bridge.sh') )
            ret = os.system(ovs_file)
            assert_equal(ret, 0)
            time.sleep(30)

      def onos_aaa_load(self):
            if self.aaa_loaded:
                  return
            aaa_dict = {'apps' : { 'org.onosproject.aaa' : { 'AAA' : { 'radiusSecret': 'radius_password',
                                                                       'radiusIp': '172.17.0.2' } } } }
            radius_ip = os.getenv('ONOS_AAA_IP') or '172.17.0.2'
            aaa_dict['apps']['org.onosproject.aaa']['AAA']['radiusIp'] = radius_ip
            self.onos_load_config('org.onosproject.aaa', aaa_dict)
            self.aaa_loaded = True

      def onos_dhcp_table_load(self, config = None):
          dhcp_dict = {'apps' : { 'org.onosproject.dhcp' : { 'dhcp' : copy.copy(self.dhcp_server_config) } } }
          dhcp_config = dhcp_dict['apps']['org.onosproject.dhcp']['dhcp']
          if config:
              for k in config.keys():
                  if dhcp_config.has_key(k):
                      dhcp_config[k] = config[k]
          self.onos_load_config('org.onosproject.dhcp', dhcp_dict)

      def onos_load_config(self, app, config):
          status, code = OnosCtrl.config(config)
          if status is False:
             log.info('JSON config request for app %s returned status %d' %(app, code))
             assert_equal(status, True)
          time.sleep(2)

      def dhcp_sndrcv(self, dhcp, update_seed = False):
            cip, sip = dhcp.discover(update_seed = update_seed)
            assert_not_equal(cip, None)
            assert_not_equal(sip, None)
            log.info('Got dhcp client IP %s from server %s for mac %s' %
                     (cip, sip, dhcp.get_mac(cip)[0]))
            return cip,sip

      def dhcp_request(self, subscriber, seed_ip = '10.10.10.1', update_seed = False):
            config = {'startip':'10.10.10.20', 'endip':'10.10.10.200',
                      'ip':'10.10.10.2', 'mac': "ca:fe:ca:fe:ca:fe",
                      'subnet': '255.255.255.0', 'broadcast':'10.10.10.255', 'router':'10.10.10.1'}
            self.onos_dhcp_table_load(config)
            dhcp = DHCPTest(seed_ip = seed_ip, iface = subscriber.iface)
            cip, sip = self.dhcp_sndrcv(dhcp, update_seed = update_seed)
            return cip, sip

      def recv_channel_cb(self, pkt):
            ##First verify that we have received the packet for the joined instance
            chan = self.subscriber.caddr(pkt[IP].dst)
            assert_equal(chan in self.subscriber.join_map.keys(), True)
            recv_time = monotonic.monotonic() * 1000000
            join_time = self.subscriber.join_map[chan][self.subscriber.STATS_JOIN].start
            delta = recv_time - join_time
            self.subscriber.join_rx_stats.update(packets=1, t = delta, usecs = True)
            self.subscriber.channel_update(chan, self.subscriber.STATS_RX, 1, t = delta)
            log.debug('Packet received in %.3f usecs for group %s after join' %(delta, pkt[IP].dst))
            self.test_status = True

      def traffic_verify(self, subscriber):
            if subscriber.has_service('TRAFFIC'):
                  url = 'http://www.google.com'
                  resp = requests.get(url)
                  self.test_status = resp.ok
                  if resp.ok == False:
                        log.info('Subscriber %s failed get from url %s with status code %d'
                                 %(subscriber.name, url, resp.status_code))
                  else:
                        log.info('GET request from %s succeeded for subscriber %s'
                                 %(url, subscriber.name))

      def tls_verify(self, subscriber):
            if subscriber.has_service('TLS'):
                  time.sleep(2)
                  tls = TLSAuthTest(intf = subscriber.rx_intf)
                  log.info('Running subscriber %s tls auth test' %subscriber.name)
                  tls.runTest()
                  self.test_status = True

      def dhcp_verify(self, subscriber):
            if subscriber.has_service('DHCP'):
                  cip, sip = self.dhcp_request(subscriber, update_seed = True)
                  log.info('Subscriber %s got client ip %s from server %s' %(subscriber.name, cip, sip))
                  subscriber.src_list = [cip]
                  self.test_status = True
            else:
                  subscriber.src_list = ['10.10.10.{}'.format(subscriber.rx_port)]
                  self.test_status = True

      def dhcp_jump_verify(self, subscriber):
            if subscriber.has_service('DHCP'):
                  cip, sip = self.dhcp_request(subscriber, seed_ip = '10.10.200.1')
                  log.info('Subscriber %s got client ip %s from server %s' %(subscriber.name, cip, sip))
                  subscriber.src_list = [cip]
                  self.test_status = True
            else:
                  subscriber.src_list = ['10.10.10.{}'.format(subscriber.rx_port)]
                  self.test_status = True

      def dhcp_next_verify(self, subscriber):
            if subscriber.has_service('DHCP'):
                  cip, sip = self.dhcp_request(subscriber, seed_ip = '10.10.150.1')
                  log.info('Subscriber %s got client ip %s from server %s' %(subscriber.name, cip, sip))
                  subscriber.src_list = [cip]
                  self.test_status = True
            else:
                  subscriber.src_list = ['10.10.10.{}'.format(subscriber.rx_port)]
                  self.test_status = True

      def igmp_verify(self, subscriber):
            chan = 0
            if subscriber.has_service('IGMP'):
                  ##We wait for all the subscribers to join before triggering leaves
                  if subscriber.rx_port > 1:
                        time.sleep(5)
                  subscriber.channel_join(chan, delay = 0)
                  self.num_joins += 1
                  while self.num_joins < self.num_subscribers:
                        time.sleep(5)
                  log.info('All subscribers have joined the channel')
                  for i in range(10):
                        subscriber.channel_receive(chan, cb = subscriber.recv_channel_cb, count = 10)
                        log.info('Leaving channel %d for subscriber %s' %(chan, subscriber.name))
                        subscriber.channel_leave(chan)
                        time.sleep(5)
                        log.info('Interface %s Join RX stats for subscriber %s, %s' %(subscriber.iface, subscriber.name,subscriber.join_rx_stats))
                        #Should not receive packets for this subscriber
                        self.recv_timeout = True
                        subscriber.recv_timeout = True
                        subscriber.channel_receive(chan, cb = subscriber.recv_channel_cb, count = 10)
                        subscriber.recv_timeout = False
                        self.recv_timeout = False
                        log.info('Joining channel %d for subscriber %s' %(chan, subscriber.name))
                        subscriber.channel_join(chan, delay = 0)
                  self.test_status = True

      def igmp_jump_verify(self, subscriber):
            if subscriber.has_service('IGMP'):
                  for i in xrange(subscriber.num):
                        log.info('Subscriber %s jumping channel' %subscriber.name)
                        chan = subscriber.channel_jump(delay=0)
                        subscriber.channel_receive(chan, cb = subscriber.recv_channel_cb, count = 1)
                        log.info('Verified receive for channel %d, subscriber %s' %(chan, subscriber.name))
                        time.sleep(3)
                  log.info('Interface %s Jump RX stats for subscriber %s, %s' %(subscriber.iface, subscriber.name, subscriber.join_rx_stats))
                  self.test_status = True

      def igmp_next_verify(self, subscriber):
            if subscriber.has_service('IGMP'):
                  for i in xrange(subscriber.num):
                        if i:
                              chan = subscriber.channel_join_next(delay=0)
                        else:
                              chan = subscriber.channel_join(i, delay=0)
                        log.info('Joined next channel %d for subscriber %s' %(chan, subscriber.name))
                        subscriber.channel_receive(chan, cb = subscriber.recv_channel_cb, count=1)
                        log.info('Verified receive for channel %d, subscriber %s' %(chan, subscriber.name))
                        time.sleep(3)
                  log.info('Interface %s Join Next RX stats for subscriber %s, %s' %(subscriber.iface, subscriber.name, subscriber.join_rx_stats))
                  self.test_status = True

      def generate_port_list(self, subscribers, channels):
            return self.port_list[:subscribers]

      def subscriber_load(self, create = True, num = 10, num_channels = 1, channel_start = 0, port_list = []):
            '''Load the subscriber from the database'''
            self.subscriber_db = SubscriberDB(create = create, services = self.test_services)
            if create is True:
                  self.subscriber_db.generate(num)
            self.subscriber_info = self.subscriber_db.read(num)
            self.subscriber_list = []
            if not port_list:
                  port_list = self.generate_port_list(num, num_channels)

            index = 0
            for info in self.subscriber_info:
                  self.subscriber_list.append(Subscriber(name=info['Name'],
                                                         service=info['Service'],
                                                         port_map = self.port_map,
                                                         num=num_channels,
                                                         channel_start = channel_start,
                                                         tx_port = port_list[index][0],
                                                         rx_port = port_list[index][1]))
                  if num_channels > 1:
                        channel_start += num_channels
                  index += 1

            #load the ssm list for all subscriber channels
            igmpChannel = IgmpChannel()
            ssm_groups = map(lambda sub: sub.channels, self.subscriber_list)
            ssm_list = reduce(lambda ssm1, ssm2: ssm1+ssm2, ssm_groups)
            igmpChannel.igmp_load_ssm_config(ssm_list)

      def subscriber_join_verify( self, num_subscribers = 10, num_channels = 1,
                                  channel_start = 0, cbs = None, port_list = []):
          self.test_status = False
          self.num_subscribers = num_subscribers
          self.subscriber_load(create = True, num = num_subscribers,
                               num_channels = num_channels, channel_start = channel_start, port_list = port_list)
          self.onos_aaa_load()
          self.thread_pool = ThreadPool(min(100, self.num_subscribers), queue_size=1, wait_timeout=1)
          chan_leave = False #for single channel, multiple subscribers
          if cbs is None:
                cbs = (self.tls_verify, self.dhcp_verify, self.igmp_verify, self.traffic_verify)
                chan_leave = True
          for subscriber in self.subscriber_list:
                subscriber.start()
                pool_object = subscriber_pool(subscriber, cbs)
                self.thread_pool.addTask(pool_object.pool_cb)
          self.thread_pool.cleanUpThreads()
          for subscriber in self.subscriber_list:
                subscriber.stop()
                if chan_leave is True:
                      subscriber.channel_leave(0)
          self.num_subscribers = 0
          return self.test_status

      def test_subscriber_join_recv(self):
          """Test subscriber join and receive for channel surfing"""
          self.num_subscribers = 5
          self.num_channels = 1
          test_status = self.subscriber_join_verify(num_subscribers = self.num_subscribers,
                                                    num_channels = self.num_channels,
                                                    port_list = self.generate_port_list(self.num_subscribers,
                                                                                        self.num_channels))
          assert_equal(test_status, True)

      def test_subscriber_join_jump(self):
          """Test subscriber join and receive for channel surfing"""
          self.num_subscribers = 5
          self.num_channels = 10
          test_status = self.subscriber_join_verify(num_subscribers = self.num_subscribers,
                                                    num_channels = self.num_channels,
                                                    cbs = (self.tls_verify, self.dhcp_jump_verify,
                                                           self.igmp_jump_verify, self.traffic_verify),
                                                    port_list = self.generate_port_list(self.num_subscribers,
                                                                                        self.num_channels))
          assert_equal(test_status, True)

      def test_subscriber_join_next(self):
          """Test subscriber join next for channel surfing"""
          self.num_subscribers = 5
          self.num_channels = 10
          test_status = self.subscriber_join_verify(num_subscribers = self.num_subscribers,
                                                    num_channels = self.num_channels,
                                                    cbs = (self.tls_verify, self.dhcp_next_verify,
                                                           self.igmp_next_verify, self.traffic_verify),
                                                    port_list = self.generate_port_list(self.num_subscribers,
                                                                                        self.num_channels))
          assert_equal(test_status, True)
