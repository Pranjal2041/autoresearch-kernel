# Rules

You are optimizing the three numbers in `PARAMS` inside `solution.py` in your
workspace. The objective is `val_loss`, mean squared error against hidden
targets. Lower is better.

- You may only change `solution.py`.
- Checkpoint your work by POSTing to `$AR_API_URL/submit` with a JSON body
  `{"notes": "<what you changed>"}`.
- Poll `$AR_API_URL/submit/<id>` until the status is `scored`, then decide
  your next move. `GET $AR_API_URL/history` shows all past attempts.
