#!/bin/bash
WHEN=$(python3 -c "import yaml; print(yaml.load(open('config.yml').read(), Loader=yaml.SafeLoader)['crontab'])")
echo "$WHEN root $(cat crontab.what)" > /etc/cron.d/maxrelax
exec cron -f -L 15
