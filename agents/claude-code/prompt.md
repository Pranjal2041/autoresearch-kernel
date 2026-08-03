# Your task this iteration

You are one iteration of an autoresearch loop. Your context will be gone
after this turn: the submit history above and your notes file are your only
memory.

1. Read `notes.md` in your workspace if it exists: it is your journal from
   past iterations.
2. Study the submit history: what was tried, what scored well, what failed.
3. Pick ONE focused change that you expect to improve the objective. Do not
   attempt several ideas at once; a submit should isolate one hypothesis.
4. Implement it in your workspace. You may run anything you like here to
   check your work before submitting.
5. Quiesce (stop background processes you started), then checkpoint:

       curl -s -X POST "$AR_API_URL/submit" \
            -H 'Content-Type: application/json' \
            -d '{"notes": "<one line: the hypothesis you tested>"}'

   If it returns 409, a previous eval is still running: wait and retry.
6. Poll `"$AR_API_URL/submit/<id>"` every few seconds until `status` is
   `scored` or `failed`. A `failed` status means your code broke the eval:
   read the error, fix it, and submit again.
7. Update `notes.md` for the next iteration: what you tried, the score, what
   to explore next. Keep it short and useful.
8. End your turn.
