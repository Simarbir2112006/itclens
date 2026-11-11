# ITCLens

Small CA practices and traders in India have to reconcile the GSTR-2B statement from the GST portal against their own purchase register before claiming input tax credit, and this is usually done by hand in spreadsheets. ITCLens takes a GSTR-2B JSON and a purchase register (Excel or CSV), validates both, and reports which invoices match, which differ, and which are missing on either side.

Status: Work in progress.

## Local setup

Requires Python 3.11 or newer.

```
make install
make check
```

The locale test needs the de_DE.UTF-8 locale installed to run; otherwise it is skipped.

## Privacy

Uploaded files are processed in memory and are not stored.

## Not tax advice

This tool does not give tax advice. Verify rules and rates with your CA.

## License

To be decided.
