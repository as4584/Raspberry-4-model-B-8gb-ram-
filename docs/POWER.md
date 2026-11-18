# Power loss and shutdown (S8)

**The Pi 4 has no battery.** When power is cut it stops instantly. No software can shut it down "gracefully" without stored energy, and there's no battery level or time-left reading to show (verified: no `/sys/class/power_supply` devices, no I2C UPS).

## What makes a sudden power cut harmless today
- **Bookings:** SQLite WAL with `synchronous=FULL` on every connection (pinned by `tests/test_power_loss.py`). A booking is only confirmed to the caller after its commit is fsynced.
- **Crash tests (POSIX, CI and Pi):** a `kill -9` mid-stream ×3 timings loses **no** booking that was reported after commit; `integrity_check` is ok, with no overlaps; a crash while holding the write lock recovers on the next open.
- **Filesystem:** ext4 `noatime`, with `fsck.repair=yes` on the kernel command line (auto-repair at boot).
- **Errors:** a locked database now surfaces as `database is locked`, not a misleading secondary `cannot rollback`.

## Options that need hardware or owner approval
| Option | Gives you | Needs |
|---|---|---|
| UPS HAT (e.g. PiSugar 3 Plus, Waveshare UPS HAT) | Battery %, time-left estimate, automatic clean shutdown at low battery | Hardware purchase; then a small monitor service + status endpoint |
| Shutdown button (`dtoverlay=gpio-shutdown`) | Press-to-shutdown, press-to-wake | Button on GPIO3/GND; a `config.txt` edit (owner approval) |
| Read-only root / overlayfs | Root fs can't be corrupted by power loss | Separate writable data partition; bigger change |

A process crash in CI stands in for a power cut. It proves atomicity, not the USB drive's write-cache honesty. A real pull-the-plug test on the Pi is still pending owner approval.
