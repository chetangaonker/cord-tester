import json
import requests
import os,sys,time

class OnosCtrl:

    auth = ('karaf', 'karaf')
    controller = os.getenv('ONOS_CONTROLLER_IP') or 'localhost'
    cfg_url = 'http://%s:8181/onos/v1/network/configuration/' %(controller)
    applications_url = 'http://%s:8181/onos/v1/applications' %(controller)

    def __init__(self, app, controller = None):
        self.app = app
        if controller is not None:
            self.controller = controller
        self.app_url = 'http://%s:8181/onos/v1/applications/%s' %(self.controller, self.app)
        self.cfg_url = 'http://%s:8181/onos/v1/network/configuration/' %(self.controller)
        self.auth = ('karaf', 'karaf')

    @classmethod
    def config(cls, config):
        if config:
            json_data = json.dumps(config)
            resp = requests.post(cls.cfg_url, auth = cls.auth, data = json_data)
            return resp.ok, resp.status_code
        return False, 400

    def activate(self):
        resp = requests.post(self.app_url + '/active', auth = self.auth)
        return resp.ok, resp.status_code

    def deactivate(self):
        resp = requests.delete(self.app_url + '/active', auth = self.auth)
        return resp.ok, resp.status_code

    @classmethod
    def get_devices(cls):
        url = 'http://%s:8181/onos/v1/devices' %(cls.controller)
        result = requests.get(url, auth = cls.auth)
        if result.ok:
            devices = result.json()['devices']
            return filter(lambda d: d['available'], devices)

        return None

    @classmethod
    def get_flows(cls, device_id):
        url = 'http://%s:8181/onos/v1/flows/' %(cls.controller) + device_id
        result = requests.get(url, auth = cls.auth)
        if result.ok:
            return result.json()['flows']
        return None

    @classmethod
    def cord_olt_config(cls, olt_device_data = None):
        '''Configures OLT data for existing devices/switches'''
        if olt_device_data is None:
            return
        did_dict = {}
        config = { 'devices' : did_dict }
        devices = cls.get_devices()
        if not devices:
            return
        device_ids = map(lambda d: d['id'], devices)
        for did in device_ids:
            access_device_dict = {}
            access_device_dict['accessDevice'] = olt_device_data
            did_dict[did] = access_device_dict

        ##configure the device list with access information
        return cls.config(config)

    @classmethod
    def install_app(cls, app_file, onos_ip = None):
        params = {'activate':'true'}
        headers = {'content-type':'application/octet-stream'}
        url = cls.applications_url if onos_ip is None else 'http://{0}:8181/onos/v1/applications'.format(onos_ip)
        with open(app_file, 'rb') as payload:
            result = requests.post(url, auth = cls.auth,
                                   params = params, headers = headers,
                                   data = payload)
        return result.ok, result.status_code
