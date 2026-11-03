"""Load the monitor configuration (config/monitor.yaml) and environment settings."""
import os
import re
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(os.environ.get('GPMON_CONFIG', ROOT / 'config' / 'monitor.yaml'))


@lru_cache
def load():
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding='utf-8'))
    cfg['_own_re'] = re.compile(cfg['client']['own_pattern'])  # case-sensitive on purpose
    brand_res = {}
    for name, b in cfg['brands'].items():
        brand_res[name] = cfg['_own_re'] if name == cfg['client']['own_brand'] else re.compile(b['pattern'], re.I)
    cfg['_brand_res'] = brand_res
    cfg['_domain_to_brand'] = {b['domain']: n for n, b in cfg['brands'].items() if b.get('domain')}
    return cfg


def env(name, default=None, required=False):
    v = os.environ.get(name, default)
    if required and not v:
        raise SystemExit(f'Missing environment variable {name}')
    return v


# BigQuery location of the dataset (EU keeps client data in Europe)
BQ_PROJECT = lambda: env('GCP_PROJECT_ID', required=True)
BQ_DATASET = lambda: env('BQ_DATASET', 'garagepark_monitor')
BQ_LOCATION = lambda: env('BQ_LOCATION', 'EU')
