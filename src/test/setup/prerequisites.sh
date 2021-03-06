#!/usr/bin/env bash
apt-get update
on_cord=0
if [ "$1" = "--cord" ]; then
    echo "Skipping installation of Docker and ONOS"
    on_cord=1
fi
if [ $on_cord -eq 0 ]; then
    apt-get -y install apt-transport-https ca-certificates
    apt-key adv --keyserver hkp://p80.pool.sks-keyservers.net:80 --recv-keys 58118E89F3A912897C070ADBF76221572C52609D
    if [ ! -f /etc/apt/sources.list.d/docker.list ]; then
        echo deb https://apt.dockerproject.org/repo ubuntu-trusty main |  tee /etc/apt/sources.list.d/docker.list
    fi
    apt-get update
    apt-get purge lxc-docker || true
    apt-get -y install linux-image-extra-$(uname -r)
    apt-get -y install apparmor
    echo "Installing Docker"
    apt-get -y install docker-engine
    service docker start
    echo "Verifying Docker installation"
    docker run --rm hello-world || exit 127
    docker rmi hello-world
    echo "Pulling ONOS latest and 1.5"
    docker pull onosproject/onos:latest || exit 127
    docker pull onosproject/onos:1.5 || exit 127
    apt-get -y install openvswitch-common openvswitch-switch
fi
apt-get -y install wget git python python-dev python-pip python-setuptools python-scapy python-pexpect tcpdump arping
easy_install nose
pip install -U scapy
pip install monotonic
pip install configObj
pip install -U docker-py
pip install -U pyyaml
pip install -U nsenter
pip install -U pyroute2
pip install -U netaddr
pip install -U python-daemon
pip install scapy-ssl_tls
( cd /tmp && git clone https://github.com/jpetazzo/pipework.git && cp -v pipework/pipework /usr/bin && rm -rf pipework )
## Special mode to pull cord-tester repo in case prereqs was installed by hand instead of repo
if [ "$1" = "--test" ]; then
    rm -rf cord-tester
    git clone https://github.com/opencord/cord-tester.git
fi
