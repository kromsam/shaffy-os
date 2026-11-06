# Critical-battery hibernation

The optional `power.low_battery_hibernate` setting makes UPower hibernate
directly at a chosen battery level. Enable it only after successfully testing
hibernation and resume on the machine. The public desktop leaves CachyOS's
battery policy alone when this setting is absent.

The setting requires `low_percent`, `critical_percent` and `action_percent`,
all greater than zero, at most 100, and strictly descending. They belong in
the supplement; the synthetic example is in
[the comprehensive fixture](../tests/fixtures/supplements/comprehensive/config.yaml).
UPower classifies the warning levels; the desktop controls how warnings appear.

## Apply and verify

Connect the charger and save work first. The `power` role checks that the
installed UPower supports drop-ins (1.91.0 or later) and that logind reports
hibernation available. Availability alone does not prove that resume works.
The role does not create swap, alter boot parameters or change lid-close sleep.

```sh
./bin/shaffy check
./bin/shaffy apply --system --tags power
busctl call org.freedesktop.UPower /org/freedesktop/UPower org.freedesktop.UPower GetCriticalAction
```

The action must be `Hibernate`. The role owns only
`/etc/UPower/UPower.conf.d/90-shaffy-critical-battery.conf`; it leaves the
package's configuration intact and restarts UPower when its override changes.
Read the installed file and check for any later override before relying on the
policy. A second apply should report no changes. Removing the setting and
applying the role removes its override and reloads the packaged policy.
A pending-reload record under `/var/lib/shaffy/` is written before the change
and removed only after a successful restart. Resuming an interrupted apply
therefore loads the policy even when the file is already correct.

A successful manual cycle is a prerequisite, not an automatic-action test.
With work saved and a charger at hand, verify a supervised battery-triggered
cycle separately: UPower must request hibernation and the original session must
resume. Do not keep draining the battery if the action fails. Reboot after a
kernel update before testing hibernation again.

## Evidence and sources

Reviewed on 2026-10-31. These sources support the mechanism; they do not
establish a universal safe percentage or guarantee any particular hardware.

| Source | What it establishes |
| --- | --- |
| [ArchWiki: low-battery hibernation](https://wiki.archlinux.org/title/Laptop#Hibernate_on_low_battery_level) | Describes direct hibernation at 5% or below and testing the command first. Its separate UPower example uses 20/10/5 with **HybridSleep**, not direct Hibernate. |
| [UPower change 309](https://gitlab.freedesktop.org/upower/upower/-/merge_requests/309), [release history](https://gitlab.freedesktop.org/upower/upower/-/blob/v1.91.5/NEWS) | Version 1.91.2 introduced Auto to delegate to logind, including machines using only zram. It also addressed inhibitors blocking emergency actions. |
| [systemd SleepOperation](https://www.freedesktop.org/software/systemd/man/latest/logind.conf.html#SleepOperation=), [sleep modes](https://www.freedesktop.org/software/systemd/man/latest/systemd-sleep.conf.html) | Auto can select suspend-then-hibernate. That mode initially keeps the session only in RAM and depends on a subsequent wake-up; hybrid sleep saves an image first. |
| [Omarchy issue 13256](https://github.com/omacom/omarchy/issues/13256) | A user reports exhaustion with Auto at 2%, and successful automatic direct hibernation at 5%. The issue is open; this is a user test, not an adopted upstream policy. |
| [Omarchy v4.0.6 setup](https://github.com/omacom/omarchy/blob/v4.0.6/bin/omarchy-hibernation-setup), [manual](https://github.com/omacom/omarchy/blob/v4.0.6/manual/36-system-sleep.md) | Configures swap and resume, and an ACPI RTC alarm for s2idle; advises checking reliable operation. The setup does not install a critical-battery override. |
| [Proposed fix in a fork](https://github.com/AnPod/omarchy/pull/16) | Open, unmerged proposal for a direct-hibernation override. It is not a merged Omarchy change. |
| [systemd issue 42975](https://github.com/systemd/systemd/issues/42975), [kernel fix](https://github.com/torvalds/linux/commit/f7ecfc3fe5753d10e9c4d0d7955c160ea8cbf0ea) | A related ASUS timed-wake failure was fixed in the AMD Cezanne path. A different machine or an early wake-up needs its own diagnosis. |
| [systemd issue 38193](https://github.com/systemd/systemd/issues/38193) | Reports intermittent failure to proceed from suspend to hibernation; useful follow-up, not proof of an identical cause. |

The role uses UPower's documented configuration interface, not code copied
from the proposed fork. No additional polling daemon is needed for the
battery action. Machine-specific evidence and the chosen thresholds belong
in the supplement.

## Follow upstream changes

Review the linked issues, the fork's pull request and the released Omarchy
implementation weekly, alongside UPower and systemd releases. Distinguish a
proposal from a merged change, and a merged change from one included in the
installed packages and running kernel. Report only a relevant new fix,
recommendation, regression or opportunity to retire this override; an issue
closure alone is not enough. Do not remove the override or retest sleep
automatically. A proposed replacement needs a supervised test on the machine.
