# Sample data

Real captures, kept small. Use them to understand the formats or to test parsing without hardware.

| File | What it is |
|---|---|
| `sample/raw_csi_router_link.csv` | ~4,300 raw CSI records from one node on its router link, in the esp-csi serial format. Early single-node capture. |
| `sample/console_log_node1.csv` | A short console log from an early single-node session: level, baseline, state and packet rate over time. |

**Privacy note:** the router's MAC address in the raw capture has been replaced with the placeholder `02:00:00:00:00:fe`. Router MACs can be located on public WiFi-mapping databases, so they're stripped before publishing. Nothing else was altered.

Column-by-column descriptions: [../docs/data-format.md](../docs/data-format.md)
