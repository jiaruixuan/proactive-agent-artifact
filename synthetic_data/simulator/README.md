# Simulator Configuration

Run the following command.

```bash
cd synthetic_data
python -m simulator.main --cfg_file simulator/sample_scenario.yaml --out_file test.jsonl
```

where `simulator/sample_scenario.yaml` is the scenario configuration file, and `test.jsonl` is the output file.

The configuration reads model settings from `synthetic_data/private.toml`.
