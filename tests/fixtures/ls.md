| NAME | HOSTNAME | ALIASES | USER | PORT | KEYS | OS | NOTES | GROUPS | EXCLUDE | INV | SSH | ANSIBLE |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| nas | 192.0.2.254 | storage |  | 22 | home | TrueNAS 25.04 | basement NAS |  |  | personal | ✓ | · |
| pi | 192.0.2.50 |  | pi | 2200 | home | Raspberry Pi OS 12 | two lines<br>the second \| with a pipe, "quoted" |  |  | personal | ✓ | · |
| scratch | 192.0.2.77 |  |  | 22 | home |  |  |  | ssh | personal | excluded | · |
| fw | 203.0.113.1 |  | deploy | 2222 | old, lab |  |  |  | ansible | work | ✓ | excluded |
| vault-01 | 192.0.2.30 |  | deploy | 2222 | lab | Rocky 10.1 |  | zone_app, no_auto_update |  | work | ✓ | ✓ |
| web-01 | 192.0.2.10 | www, 192.0.2.10 | admin | 22 | lab |  | front door | zone_app |  | work | ✓ | ✓ |
