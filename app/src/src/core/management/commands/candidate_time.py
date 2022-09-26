# TODO: create a working script next time we need it

from collections import defaultdict
from datetime import timedelta

from django.utils.timezone import now
from more_itertools import pairwise
from src.core.views import hubstaff

ORG_ID = 434165

projects = hubstaff.get(f'/v2/organizations/{ORG_ID}/projects')['projects']
projects = {project['id']: project['name'] for project in projects}

now_ = now()
dates = [
    (now_ + timedelta(days=start), now_ + timedelta(days=end))
    for start, end in pairwise(range(-20, 0))
]

total = defaultdict(int)
for start_date, end_date in dates:
    results = hubstaff.get(f'/v2/organizations/{ORG_ID}/activities', params={
        'page_start_id': 0, 'page_limit': 100,
        'time_slot[start]': start_date.isoformat(),
        'time_slot[stop]': end_date.isoformat(),
    })
    for result in results['activities']:
        total[projects[result['project_id']]] += result['tracked']

total = {project: secs / 60 for project, secs in total.items()}
print(total)
