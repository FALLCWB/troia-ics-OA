# External dataset captures used in Table 7

Table 7 of the article reports traffic-facing channel signals on the Frazao et al.
Cyber-security Modbus ICS dataset. The captures are not redistributed here; they are
obtained from the dataset's own distribution point, the `MODBUSTCP#1` release of
<https://github.com/tjcruz-dei/ICS_PCAPS>, archive `captures1_v2.zip`.

Selection rule, applied uniformly: for each attack scenario, the one-hour capture with
the five-minute attack period; for the reference, the one-hour clean capture. Using
equal-duration captures with the same nominal attack interval removes capture length and
attack duration as confounders and permits a like-for-like comparison across scenarios.

| Scenario | File (within `captures1_v2/`) | Bytes | SHA-256 |
|---|---|---|---|
| `clean` | `clean/eth2dump-clean-1h_1.pcap` | 6022851 | `c821daedcd276ea0066d538760a9cd69df6ca27c8e1b05ebbeee367896859197` |
| `mitm` | `mitm/eth2dump-mitm-change-5m-1h_1.pcap` | 5711313 | `d9d074e1bb1cdfab01df71248de048abc4ea1b76d4f561dc8e90da6f91bb2804` |
| `qflood` | `modbusQueryFlooding/eth2dump-modbusQueryFlooding5m-1h_1.pcap` | 20998792 | `5b50bbd2b0b1652c6ecab4c8f360c7f106240001809dfd245e12bf88907effde` |
| `q2flood` | `modbusQuery2Flooding/eth2dump-modbusQuery2Flooding5m-1h_1.pcap` | 21770641 | `752699c199be5bddd6abd3286c035f8a90b3e00ca08ef8b06c1c3194e4f32808` |
| `pingflood` | `pingFloodDDoS/eth2dump-pingFloodDDoS5m-1h_1.pcap` | 7268516 | `40c195e00a4753fe9e59a952570a1ad05154568ae288344a1fd7de6a569e8380` |

## Computed values

Non-conformant counts and function-code histograms come from `metrics/modbus_parser.py`.
The rate and inter-arrival columns are derived from the timestamps of the packets that
parser selects, since the parser reports counts rather than rates.

| Scenario | PDUs | Duration [s] | PDU rate [Hz] | Median IAT [ms] | Non-conformant [%] | FC histogram |
|---|---|---|---|---|---|---|
| `clean` | 65601 | 3599.8 | 18.2 | 8.95 | 63.04 | 0x03:23003, 0x06:1242 |
| `mitm` | 64161 | 3599.6 | 17.8 | 8.74 | 63.71 | 0x03:22043, 0x06:1242 |
| `qflood` | 231656 | 3599.4 | 64.4 | 0.25 | 78.96 | 0x03:20015, 0x06:28717 |
| `q2flood` | 237652 | 3599.6 | 66.0 | 0.25 | 78.92 | 0x03:48848, 0x06:1240 |
| `pingflood` | 31066 | 3598.1 | 8.6 | 0.25 | 96.0 | 0x06:1242 |

To reproduce: download `captures1_v2.zip` from the release above, verify the digests, then
run `python -m metrics.modbus_parser --pcap <file> --out <out.json>` for each capture.
