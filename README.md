# Plugwise USB python module

This module is the USB backend for the upcoming [`plugwise_usb` component](https://github.com/home-assistant/core/tree/dev/homeassistant/components/plugwise_usb) which we will then maintain as code owners for Home Assistant.

Our main usage for this module is supporting [Home Assistant](https://www.home-assistant.io) / [home-assistant](http://github.com/home-assistant/core/)

![Static Badge](https://img.shields.io/badge/Plugwise_Discord-Join_now-purple?style=social&logo=discord&link=https%3A%2F%2Fdiscord.gg%2FmFVhF8Ar6A)
[![Maintenance](https://img.shields.io/badge/Maintained%3F-yes-green.svg)](https://github.com/plugwise)
[![PyPI version fury.io](https://badge.fury.io/py/plugwise_usb.svg)](https://pypi.python.org/pypi/plugwise_usb/)

[![Latest release](https://github.com/plugwise/python-plugwise-usb/workflows/Latest%20release/badge.svg)](https://github.com/plugwise/python-plugwise-usb/actions)
[![Newest commit](https://github.com/plugwise/python-plugwise-usb/workflows/Latest%20commit/badge.svg)](https://github.com/plugwise/python-plugwise-usb/actions)
[![pre-commit.ci status](https://results.pre-commit.ci/badge/github/plugwise/python-plugwise-usb/main.svg)](https://results.pre-commit.ci/latest/github/plugwise/python-plugwise-usb/main)
[![renovate maintained](https://img.shields.io/badge/maintained%20with-renovate-blue?logo=renovatebot)](https://github.com/plugwise/python-plugwise-usb/issues/2)

[![CodeFactor](https://www.codefactor.io/repository/github/plugwise/python-plugwise-usb/badge)](https://www.codefactor.io/repository/github/plugwise/python-plugwise-usb)
[![codecov](https://codecov.io/gh/plugwise/python-plugwise-usb/branch/main/graph/badge.svg)](https://codecov.io/gh/plugwise/python-plugwise-usb)
[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=plugwise_python-plugwise-usb&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=plugwise_python-plugwise-usb)
[![Technical Debt](https://sonarcloud.io/api/project_badges/measure?project=plugwise_python-plugwise-usb&metric=sqale_index)](https://sonarcloud.io/summary/new_code?id=plugwise_python-plugwise-usb)
[![Code Smells](https://sonarcloud.io/api/project_badges/measure?project=plugwise_python-plugwise-usb&metric=code_smells)](https://sonarcloud.io/summary/new_code?id=plugwise_python-plugwise-usb)

## Integration

### Home-Assistant Integration

(maintained through Home-Assistant)

[![Generic badge](https://img.shields.io/badge/HA%20core-no-red.svg)](https://github.com/home-assistant/core/tree/dev/homeassistant/components/plugwise_usb)

### Home-Assistant custom_component (beta)

We do (also) maintain a `custom_component`, please note this is **only** intended for users helping us test new features (use at your own risk)

[![Generic badge](https://img.shields.io/github/v/release/plugwise/plugwise_usb-beta)](https://github.com/plugwise/plugwise_usb-beta)

You can add our `custom_component` repository to HACS, do note that we do not intent for our `beta` `custom_component` to be included in the HACS repository.

[![Generic badge](https://img.shields.io/badge/HACS-add%20our%20repo-yellow.svg)](https://github.com/plugwise/plugwise_usb-beta)

See the [`plugwise_usb-beta`](https://github.com/plugwise/plugwise_usb-beta) repository for more info.

## Development/patches

Like Home Assistant Core we use `pre-commit` and additionally run [pre-commit.ci](http://pre-commit.ci) to automatically validate your commits and PRs.

If you want to create a PR please make sure you at least run `scripts/setup.sh`. This will ensure your environment is set up correctly before attempting to `git commit`. We sincerely and highly recommended also setting up local testing, see [`tests/README.md`](https://github.com/plugwise/python-plugwise-usb/blob/main/tests/README.md) for more information and run `scripts/setup_test.sh` to prepare your environment.

## Project support status

**Notice** at this time we are refactoring the module code to move towards a supporting way for the integration to become multiple components under an umbrella `plugwise` integration featuring multiple components.

Module providing interfacing with the Plugwise devices:

### USB

- [x] Stick
  - [x] Circle+ / Stealth+
  - [x] Circle / Stealth
  - [x] Scan
  The devices listed below have **NOT** been tested and are therefore unknown for their correct operation
    - [x] Sense
    - [x] Switch

- [x] [Home-Assistant](https://home-assistant.io) via
  - [ ] Native support for USB Plugwise products in HA Core (in progress)
  - [x] [HACS](https://hacs.xyz) and `custom_component` [Plugwise USB Beta](https://github.com/plugwise/plugwise_usb-beta/) (supporting all devices above)
  
## License, origins and contributors

As per the origins we have retained the appropriate licensing and including the MIT-license for this project.

Origins (from newest to oldest):

- USB module and integration is supported by @brefra (Frank), ssupported and helped by @bouwew (Bouwe) and @CoMPaTech (Tom) where possible.
- 'All' available Plugwise support by @bouwew (Bouwe), @brefra (Frank) and @CoMPaTech (Tom)
- Upstreamed haanna/HA-core Anna, including all later products - 'Plugwise-Smile/Plugwise-HA/plugwise-beta` by @bouwew (Bouwe) & @CoMPaTech (Tom)
- Networked Plugwise Anna module with custom_module - `haanna/anna-ha` via <https://github.com/laetificat> (Kevin)
- USB-based stick module with custom_module - `plugwise-stick/plugwise` by @brefra (Frank)
- USB-plugwise module - `plugwise` by <https://github.com/cyberjunky/python-plugwise> (Ron) originally by <https://github.com/aequitas/python-plugwise> (Johan) (with reference only in license to Sven)
- Sensor for Plugwise Smile P1 integration - `home-assistant-sensor-plugwise-smile-p1` by <https://bitbucket.org/jvdschoot/home-assistant-sensor-plugwise-smile-p1> (Jeroen van der Schoot)

## Thanks

On behalf all of us, big thanks to Plugwise and community members @riemers and @tane from [HAshop](https://hashop.nl) for their support and obviously all our users and testers who dealt with our typos and challenges. Disclaimer, while we are communicating with Plugwise and they expressed their gratitude through their newsletter, we are not part of Plugwise as a company. We are just a bunch of guys anxious to get our (and your) Plugwise products working with Home Assistant.
