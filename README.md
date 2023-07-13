hubstaff-api-mutator
===============================

A proxy which provides a tangled API to candidates and sends requests back and forth to Hubstaff.

The `ApiMixer` class is capable of reading swagger file and generating "permuted" swagger file based on
set of permutation functions and seed for randomizer. For each user, his ID is used as mixer's seed,
and all user's permuted paths start with `/v{user_id}/...`, so that we may easily test users' solutions.

To start the permutator, just launch django as usual and create an account for each candidate. Candidates
will be able to login and see their personalized Hubstaff API definitions.

For better understanding under-the-hood logic, please see `ApiMixer`'s docstring. Probably.



Skeleton of this project was generated with `cookiecutter-rt-django`, which sometimes gets upgrades that are easy to retrofit into already older projects.

Set Jira API access credentials
------------

When a candidate uploads his solution in `/submit/` page, it is attached to a Jira issue that is created automatically for each candidate.
To enable this feature, we need to set credentials of one person who will be the reporter of that issue.

For better understanding, please see `JiraV3`'s docstring.

Base requirements
------------

* docker
* docker-compose
* direnv (https://direnv.net/ - installation and setup instruction)
* Python 3.8

For a fresh ubuntu you can install the above with:
```
groupadd docker
snap install docker
apt install direnv
```


Setup virtualenv (for development)
----------------------------------

```
$ mkvirtualenv -p /usr/bin/python3.8 src
$ ./setup-virtualenv.sh

# on second tab

$ docker-compose up

# wait till db is initialized, then on first tab

$ cd app/src
$ python manage.py migrate
$ python manage.py runserver

```

Setup production (docker deployment)
------------------------------------

Use `ssh-keygen` to generate a key pair for the server, then add read-only access to repository in "deployment keys" section (`ssh -A` is easy to use, but not safe).

```
./setup-docker-prod.sh

# change SECRET_KEY and (POSTGRES_PASSWORD or DATABASE_URL) in `.env`, adjust the rest of `.env` and `.envrc` to your liking

# set correct NGINX_HOSTNAME if .env for https

$ ./letsencrypt_setup.sh

$ docker-compose up

# wait till db is initialized then Ctrl + C

$ ./deploy.sh

```


Setting up backups
------------------

Add `cd hubstaff-api-mutator; hubstaff-api-mutator/bin/backup-to-email.sh target@email.address` to crontab. This assumes you have configured email access in `.env` properly.


Handling requirements freeze
----------------------------

Using `./deploy.sh` on production usually runs rebulding python packages.
This can cause errors when there is a new version of a package that is required
by "main" dependency (like `kombu` for `celery` https://stackoverflow.com/questions/50444988/celery-attributeerror-async-error). To prevent this `./app/src/requirements_freeze.py`
script is provided. This script freezes `requirements.txt` using `pip freeze`
on virtualenv, but keeps "main" depedencies separate from freezed ones (using
`# -- pip freezed` comment). Additionally it scans "main" dependencies for their
requirements and adds only those packages that are required by "main" dependecies.
This allows to run script in virtualenv with development packages installed (like
`ipython`, `flake8`, `yapf` etc.).

To use `requirements_freeze.py` script just activate virtualenv, install packages
using `pip install -r requirements.txt` and then run `./requirements_freeze.py`.
It can take a while (even more than 60s) but it would not be run often.

To add new "main" dependecy to project, just install package using `pip` and
add package to `requirements.txt` above `# -- pip freezed` comment with freezed
version (`package-name==x.x.x`). Then run `requirements_freeze.py`.

To upgrade a package just upgrade it using `pip install --upgrade package-name`
and then run `requirements_freeze.py` - script will update "main" package version
in `requirements.txt` file.

There is one limitation - main dependecies needs to be provided with freezed version
(`package-name==x.x.x`) - all other notation is considered "custom" dependecy
(like github commit, etc.) and is processed without freezing version. Additionally
if there is a match for package name in custom notation (eg. git+https://github.com/django-recurrence/django-recurrence.git@7c6fcdf26d96032956a14fc9cd6841ff931a52fe#egg=django-recurrence)
then package depedencies are freezed (but custom package entry is left without change).
Notations like `package-name>=x.x.x` or `package-name` (without version) are considered
custom and **should not be used** - all dependecies should be freezed - either by
`requirements_freeze.py` script or by github commit/tag reference
(or any equivalent - **branch reference is not freezing version**)

Restoring system from backup after a catastrophical failure
-----------------------------------------------------------
1. Follow the instructions above to set up a new production environment
2. Restore the database using bin/restore-db.sh
3. See if everything works
4. Set up backups on the new machine
5. Make sure everything is filled up in .env, error reporting integration, email accounts etc
