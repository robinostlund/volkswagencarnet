# e-Golf responses

These responses were obtained from a 2020 e-Golf based in the UK.

There was an error with the VW charging API at the time, so the `chargeMode` entry in `selectivestatus_by_app.json` is faulty:

```json
"chargeMode": {
"error": {
    "message": "Bad Gateway",
    "errorTimeStamp": "2023-12-21T17:46:28Z",
    "info": "Upstream service responded with an unexpected status. If the problem persists, please contact our support.",
    "code": 4111,
    "group": 2,
    "retry": true
}
}
```

I can update this bit once the service is working again.
