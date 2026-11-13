# PlayStation 5 Local

Monitor and control a PlayStation 5 from Home Assistant over your local network. Each console gets its own device and stable entities. The integration uses Remote Play discovery and control directly, with no MQTT broker or separate app.

| Entity | Behavior |
| --- | --- |
| Power state | Awake or network-enabled rest mode. The entity becomes unavailable if the console stops answering. |
| Game or app | The name reported by the console. The value is unknown when the console omits it. |
| Title ID | Optional diagnostic sensor for the exact title identifier, when reported. |
| Power switch | Wakes a paired console or puts it into rest mode. Home Assistant waits for the console to report the requested state. |
| Home screen | Optional button for a paired, awake console. Enable it in the device's entity list. Remote Play does not report whether the screen changed. |

<!-- ha-marketplace:exclude-start -->
## Install

On Home Assistant 2026.11 or newer, add the GitHub repository as a custom integration in **Settings → Marketplace**. On Home Assistant 2026.10.4, add it through **HACS → Custom repositories** as an Integration. For manual installation, extract the release ZIP into `custom_components/ps5_local/` and restart Home Assistant.

The repository and release ZIP use the HACS integration format. See the minimum Home Assistant version in `hacs.json`.
<!-- ha-marketplace:exclude-end -->

## Set up the console

1. On PS5, enable **Settings → System → Remote Play → Enable Remote Play**. To wake it from rest mode, turn on **Stay Connected to the Internet** and **Enable Turning On PS5 from Network** under **Settings → System → Power Saving → Features Available in Rest Mode**.
2. In Home Assistant, open **Settings → Devices & services → Add integration → PlayStation 5 Local**. Search the LAN or enter the console's IPv4 address manually. Use a DHCP reservation for stable addressing, or use **Reconfigure** to change the address.
3. Choose **Monitor only** to start without PSN. For power and Home controls, choose **Pair for power control**. You can also pair an existing monitoring entry through its integration options. On the awake console, open **Link Device** and enter the eight-digit PIN in Home Assistant. Enter either a Remote Play account ID or the redirect URL from the Sony sign-in link shown by the wizard. If your console profile uses a four-digit login passcode, set it in the integration options.

Normal polling and control stay on the LAN. Using the Sony sign-in link contacts Sony during setup to resolve the account ID. Home Assistant retains the derived pairing data and any optional profile passcode in its local configuration storage. Treat Home Assistant configuration backups as sensitive.

For a migration, run monitoring alongside the existing integration first. After checking wake and rest mode, retarget the automations that depend on the old entities. Check their behavior before removing the old integration.

## Troubleshooting

- Try manual IPv4 entry if broadcast discovery returns nothing. Both hosts must have a route for local Remote Play. Fully powered-off consoles may be unavailable.
- If standby or Home is rejected, check for an active Remote Play session or a required console profile passcode.
- Some firmware and console states omit game or app details. The power sensor can still be used when those fields are absent.
- Diagnostic exports contain availability, power state, firmware and whether pairing exists. They omit pairing data, IP and activity.

## Development and release

Run `python -m pytest -q` with Home Assistant Core 2026.11.1 and the dependencies from `requirements-test.txt`. Build the release asset with `python tools/build_release.py --tag v0.1.0`. Attach `dist/ps5_local.zip` to the matching GitHub Release. The default catalog submission requires a published release and passing HACS and hassfest checks. [Marketplace catalog guide](https://contributors.home-assistant.io/marketplace/guides/catalog/)

The power control and registration library is [ps5-remoteplay](https://github.com/iharosi/ps5-remoteplay), based on playactor and Chiaki. The optional Home request uses a frame documented in the [playdirector 0.3.0 source distribution](https://pypi.org/project/playdirector/0.3.0/).


AGPL-3.0-only. This project is independent of Sony Interactive Entertainment.
