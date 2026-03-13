te-presseverteiler-agent/
├── README.md
├── AGENTS.md
├── requirements.txt
├── .gitignore
├── config/
│   ├── media_targets.csv
│   └── mandate_mapping.csv
├── data/
│   └── master/
│       └── MASTER_DACHLILUX.xls
├── reports/
├── snapshots/
├── src/
│   ├── crawler.py
│   ├── parser.py
│   ├── diff_engine.py
│   ├── matcher.py
│   ├── scorer.py
│   ├── reporter.py
│   ├── updater.py
│   └── pipeline.py
└── .github/
    └── workflows/
        └── hourly_scan.yml
