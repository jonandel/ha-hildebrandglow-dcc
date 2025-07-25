# Hildebrand Glow (DCC) Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Default-orange.svg?style=for-the-badge)](https://github.com/custom-components/hacs)
[![CodeFactor Grade](https://img.shields.io/codefactor/grade/github/JonandEl/ha-hildebrandglow-dcc?style=for-the-badge)](https://www.codefactor.io/repository/github/jonandel/ha-hildebrandglow-dcc)
[![DeepSource](https://deepsource.io/gh/jonandel/ha-hildebrandglow-dcc.svg/?label=active+issues&show_trend=true&token=gYN6CNb5ApHN5Pry_U-FFSYK)](https://deepsource.io/gh/JonandEl/ha-hildebrandglow-dcc/?ref=repository-badge)
[!["Buy the author A Coffee"](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/jonandel)

Home Assistant integration for energy consumption data from UK SMETS (Smart) meters using the Hildebrand Glow API.
The original from (HandyHat) of this project is now declared as abandoned, so after the local tweaks Ive been making over the last 24 months, and into the future, I decided to create my own fork, and release it.
The idea is that this new Repo will be a place where the community of users can contribute, and collectively we can improve this Integration.

## Installation

### Automated installation through HACS

You can install this component through [HACS](https://hacs.xyz/) to easily receive updates. Once HACS is installed, click this link:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=jonandel&repository=ha-hildebrandglow-dcc)

<details>
  <summary>Manually add to HACS</summary>
  Visit the HACS Integrations pane and go to <i>Explore and download repositories</i>. Search for <code>Hildebrand Glow (DCC)</code>, and then hit <i>Download</i>. You'll then be able to install it through the <i>Integrations</i> pane.
</details>

### Manual installation

Copy the `custom_components/hildebrandglow_dcc/` directory and all of its files to your `config/custom_components/` directory.

## Configuration

Once installed, restart Home Assistant:

[![Open your Home Assistant instance and show the system dashboard.](https://my.home-assistant.io/badges/system_dashboard.svg)](https://my.home-assistant.io/redirect/system_dashboard/)

Then, add the integration:

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=hildebrandglow_dcc)


<details>
  <summary>Manually add the Integration</summary>
  Visit the <i>Integrations</i> section in Home Assistant and click the <i>Add</i> button in the bottom right corner. Search for <code>Hildebrand Glow (DCC)</code> and input your credentials. <b>You may need to clear your browser cache before the integration appears in the list.</b>
</details>

## Sensors

Once you've authenticated, the integration will automatically set up the following sensors for each of the smart meters on your account:

- Usage (Today) - Consumption today (kWh)
- Cost (Today) - Total cost of today's consumption (GBP)
- Standing Charge - Current standing charge (GBP)
- Rate - Current tariff (GBP/kWh)

The usage and cost sensors will still show the previous day's data until shortly after 01:00 to ensure that all of the previous day's data is collected.

The standing charge and rate sensors are disabled by default as they are less commonly used. Before enabling them, ensure the data is visible in the Bright app.

If the data being shown is wrong, check the Bright app first. If it is also wrong there, you will need to contact your supplier and tell them to fix the data being provided to DCC Other Users, as Bright is one of these.

## Energy Management

The sensors created were originally designed to integrate directly into Home Assistant's [Home Energy Management](https://www.home-assistant.io/docs/energy/).  However, with HA since (roughly 2024), the energy dashboard does not handle the daily resetting of the sensors.  
It is NOT recommended you use the daily usage and cost sensors in the Energy integration, and consider using the Utility Meter Integration (built into HA), and define you own total usage and Cost sensors.  A later release of this integration will update on suggestions on what and how to do this - but it is currently believed to be NOT something this integration can resolve directly.

Be aware also that due to the lower refresh rate of this Integration, you will need to wait until the sensors have data - so wait an hour or two.

[![Open your Home Assistant instance and show your Energy configuration panel.](https://my.home-assistant.io/badges/config_energy.svg)](https://my.home-assistant.io/redirect/config_energy/)

## Debugging

To debug the integration, add the following to your `configuration.yaml`

```yaml
logger:
  default: warning
  logs:
    custom_components.hildebrandglow_dcc: debug
```

## Development

To begin, it is recommended to create a virtual environment to install dependencies:

```bash
python -m venv dev-venv
. dev-venv\Scripts\activate
```

You can then install the dependencies that will allow you to develop:
`pip3 install -r requirements-dev.txt`

This will install `black`, `homeassistant`, `isort`, `pyglowmarkt` and `pylint`.

### Code Style

This project makes use of black, isort and pylint to enforce a consistent code style across the codebase.

## Credits

Thanks go to:

- The Hildebrand API [documentation](https://docs.glowmarkt.com/GlowmarktAPIDataRetrievalDocumentationIndividualUserForBright.pdf) and [Swagger UI](https://api.glowmarkt.com/api-docs/v0-1/resourcesys/).

- The [original project](https://github.com/HandyHat/ha-hildebrandglow) from which this project is forked.

- All of the contributors and users, without whom this integration wouldn't be where it is today.
