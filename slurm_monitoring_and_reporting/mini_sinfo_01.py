"""
Rewrite mini_sinfo_00.py but have it be responsible for only one cluster.

Everything that comes out of this endpoint will have a prefix given
by --endpoint_prefix, which defaults to `cluster_name` followed by an underscore.
Note that "not specifying it" is different from setting it to be
the empty string --endpoint_prefix="". 

"""

import os
import time
import json
import re
from datetime import datetime
from collections import defaultdict

import numpy as np
from prometheus_client import start_http_server
from prometheus_client import Enum, Summary, Gauge

# https://docs.paramiko.org/en/stable/api/client.html
from paramiko import SSHClient, AutoAddPolicy

import slurm_monitoring_and_reporting
from slurm_monitoring_and_reporting.common import (filter_unrelated_jobs, )
from slurm_monitoring_and_reporting.state_managers import (NodeStatesManager, ReservationStatesManager, JobStatesManager)


import argparse
parser = argparse.ArgumentParser(description='Prometheus endpoint that exposes information from pyslurm.')
parser.add_argument('--port', type=int,
                    help='port for the prometheus endpoint')
parser.add_argument('--cluster_name', type=str, default="mila",
                    help='one of ["mila", "beluga", "graham", "cedar"]')
parser.add_argument('--endpoint_prefix', type=str, default=None,
                    help='to tell apart prometheus endpoints, we need to have a different prefixes')
parser.add_argument('--refresh_interval', type=int, default=5*60,
                    help='interval between fetches')
# for testing
parser.add_argument('--mock_data_dir', type=str, default=None,
                    help='instead of pyslurm.job().get(), read job.json, node.json, reservation.json from this directory')

args = parser.parse_args()
if args.endpoint_prefix is None:
    args.endpoint_prefix = args.cluster_name + "_"

# This is hardcoded, but it should be stored as a configuration file
# specified as argument to the script.
DD_cluster_desc = {
    "beluga":
        {"name": "beluga",
        "cmd" : 'module load python/3.8.2; python3 ${HOME}/bin/sinfo_scraper.py ',
        "hostname": "beluga.computecanada.ca",
        "username": "alaingui",
        "port": 22,
        # TODO : Find these values.
        "slurm_login_nodes": [ ],
        "slurm_partitions": ['c-gput4', 'gpubase_bygpu_b3', 'gpubase_bygpu_b2',
            'cpubase_bycore_b4', 'cpularge_bycore_b5', 'cpubase_bycore_b3',
            'cpularge_bynode_b4', 'cpularge_bycore_b3', 'gpubase_interac',
            'c-slarge', 'cpubackfill', 'gpubase_bygpu_b1', 'gpubase_bynode_b4',
            'cpubase_interac', 'gpubackfill', 'cpubase_bynode_b1', 'cpularge_bynode_b1',
            'cpularge_bycore_b4', 'cpularge_interac', 'cpularge_bycore_b2',
            'cpularge_bynode_b3', 'c-dragen', 'cpubase_bycore_b2', 'gpubase_bynode_b5',
            'cpubase_bynode_b5', 'cpularge_bynode_b2', 'cpularge_bycore_b1', 'gpubase_bynode_b3',
            'cpubase_bycore_b5', 'gpubase_bynode_b1', 'cpubase_bynode_b2', 'cpularge_bynode_b5',
            'gpubase_bynode_b2', 'cpubase_bynode_b4', 'cpubase_bynode_b3', 'gpubase_bygpu_b4',
            'cpubase_bycore_b1', 'gpubase_bygpu_b5'],
         },
    "mila":
        {"name": "mila",
        "cmd" : 'source ${HOME}/Documents/code/venv38/bin/activate; python3 ${HOME}/bin/sinfo_scraper.py ',
        "hostname": "login.server.mila.quebec",
        "username": "alaingui",
        "port": 2222,
        # Mila-specific values.
        # These should be read from a configuration file instead of from the source code.
        # We should figure out also what are the equivalent values for beluga and other clusters.
        "slurm_login_nodes": [ "login-1", "login-2", "login-3", "login-4"],
        "slurm_partitions": [   "unkillable","short-unkillable","main","main-grace",
                                "long","long-grace","cpu_jobs","cpu_jobs_low","cpu_jobs_low-grace"]}
}


# Filters
# slurm_ignore_partitions=["gcpDebug", "cloud_tpux1Cloud", "gcpDevCPUCloud", "gcpMicroMemCPUCloud", "gcpMiniMemCPUCloud", "gcpComputeCPUCloud", "gcpGeneralCPUCloud", "gcpMemoryCPUCloud", "gcpV100x1Cloud", "gcpV100Xx1Cloud", "gcpV100x2Cloud", "gcpV100x4Cloud", "gcpK80x1Cloud", "gcpK80Xx1Cloud", "gcpK80x2Cloud", "gcpK80x4Cloud"]




class SinfoManager:

    def __init__(self, cluster_name, endpoint_prefix, mock_data_dir=None):

        self.cluster_name = cluster_name
        self.endpoint_prefix = endpoint_prefix
        # this can be used for testing without bothering the production machines
        self.mock_data_dir = mock_data_dir

        # alternatively will be fetched from the config file
        self.D_cluster_desc = DD_cluster_desc[cluster_name]

        self.ssh_client = None
        self.node_states_manager = NodeStatesManager(cluster_name, endpoint_prefix)
        self.reservation_states_manager = ReservationStatesManager(cluster_name, endpoint_prefix)
        self.job_states_manager = JobStatesManager(cluster_name, endpoint_prefix, self.D_cluster_desc)

    def open_connection(self):
        if self.mock_data_dir is not None:
            # skip doing anything
            return
        clds = self.D_cluster_desc
        self.ssh_client = SSHClient()
        self.ssh_client.set_missing_host_key_policy(AutoAddPolicy())
        self.ssh_client.load_system_host_keys()
        self.ssh_client.connect(clds["hostname"], username=clds["username"], port=clds["port"])

    def close_connection(self):
        """
        No need to keep those connections open between polling intervals.
        Prometheus is set for 30 seconds intervals, but this seems aggressive
        when it comes to Compute Canada clusters.
        We'd rather use 5 minutes intervals and close the connections each time.
        """
        if self.mock_data_dir is not None:
            # skip doing anything
            return        
        self.ssh_client.close()

    def fetch_data(self):
        """

        """

        if args.mock_data_dir is not None and os.path.exists(args.mock_data_dir):
            # Read data from files (mostly for testing purposes).

            print(f"Will use mock data from {args.mock_data_dir}.")
            with open(os.path.join(args.mock_data_dir, "node.json"), "r") as f:
                psl_nodes = json.load(f)
            with open(os.path.join(args.mock_data_dir, "reservation.json"), "r") as f:
                psl_reservations = json.load(f)
            with open(os.path.join(args.mock_data_dir, "job.json"), "r") as f:
                psl_jobs = json.load(f)
        else:
            # Read the information for real from pyslurm on a remote machine.

            ssh_stdin, ssh_stdout, ssh_stderr = self.ssh_client.exec_command(self.D_cluster_desc["cmd"])
            # print(ssh_stdout.readlines())
            try:
                E = json.loads(" ".join(ssh_stdout.readlines()))
                psl_nodes, psl_reservations, psl_jobs = (E['node'], E['reservation'], E['job'])
            except Exception as inst:
                print(type(inst))    # the exception instance
                print(inst.args)     # arguments stored in .args
                print(inst)
                psl_nodes, psl_reservations, psl_jobs = (None, None, None)
                # Probably better to quit than to try to salvage this right now.
                # In production, we probably want to be more lenient in case
                # there's just a blip in the network.
                # We can also have nice gauges to indicate whether we succeeded
                # or failed to fetch that information for particular clusters,
                # which should be informative in its own right.
                quit()

        # Remove non-bengio non-mila jobs.
        psl_jobs = filter_unrelated_jobs(psl_jobs)
        # No matter where the data came from, now we want to process it.
        self.node_states_manager.update(psl_nodes)
        self.reservation_states_manager.update(psl_reservations)
        self.job_states_manager.update(psl_jobs)

"""
These "state managers" are a way to encapsulate the processing done
to the data read from pyslurm. There are a lot of specific rules that
were applied in "sinfo.py". We need to be able to isolate them and
discuss them independently of each other.
"""


def run():
    port = args.port
    # prom_metrics = {'request_latency_seconds': Summary('request_latency_seconds', 'Description of summary')}

    sima = SinfoManager(args.cluster_name, args.endpoint_prefix, args.mock_data_dir)

    start_http_server(port)
    while True:
        sima.open_connection()
        sima.fetch_data()
        sima.close_connection()
        time.sleep(args.refresh_interval)

if __name__ == "__main__":
    run()


"""
export PYTHONPATH=${PYTHONPATH}:${HOME}/Documents/code/slurm_monitoring_and_reporting
source ${HOME}/Documents/code/venv38/bin/activate

python3 -m slurm_monitoring_and_reporting.mini_sinfo_01 --port 17001 --cluster_name mila --endpoint_prefix "mila_" --refresh_interval 30 \
    --mock_data_dir ${HOME}/Documents/code/slurm_monitoring_and_reporting/misc/mila_cluster
python3 -m slurm_monitoring_and_reporting.mini_sinfo_01 --port 17002 --cluster_name beluga --endpoint_prefix "beluga_" --refresh_interval 30 \
    --mock_data_dir ${HOME}/Documents/code/slurm_monitoring_and_reporting/misc/beluga


"""