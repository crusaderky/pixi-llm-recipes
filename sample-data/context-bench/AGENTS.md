# Instructions

You will be given the full text of a book. The text ends with a `QUESTIONS`
section containing 20 questions, labelled `Q1` through `Q20`. Each question
asks about a strict, unambiguous fact (a name, date, number, place, or title)
stated somewhere in the book.

Answer the questions according to these rules:

1. **Use only the supplied text.** Every answer is contained somewhere in the
   book above the `QUESTIONS` section. Do not rely on outside knowledge, and do
   not guess from the title or author — find the fact in the text.
2. **One unambiguous answer per question.** Each question is written so that
   exactly one answer is correct. Follow the output format requested in the
   question (for example, "a number only", "a 4-digit year", "First Last",
   "Month Day", or "exactly as written in the text").
3. **Output format.** Reply with one answer per line, each labelled with its
   question number, from `A1` to `A20`:

   ```
   A1. <answer>
   A2. <answer>
   ...
   A20. <answer>
   ```

   Give only the answer in the requested format. No reasoning, no quotes, no
   citations, no extra prose.
4. **If you do not know an answer, leave it blank.** Emit the label with
   nothing after it rather than guessing. For example, if you cannot answer
   question 2:

   ```
   A2.
   ```

5. **No chain-of-thought in the output.** Think internally if you must, but
   emit only the 20 labelled answers, `A1` through `A20`.
