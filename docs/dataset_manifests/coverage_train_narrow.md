# Coverage train/narrow/seed 20261118

- examples: 4000
- pairwise coverage: 84/760 = 0.111

| factor=value | count |
|---|---|
| correction=none | 2497 |
| date_style=iso | 1234 |
| date_style=month_day | 1263 |
| duration_style=minutes | 1241 |
| duration_style=unspecified | 1256 |
| edge=normal | 2497 |
| expected=answer_faq | 382 |
| expected=book_appointment | 1005 |
| expected=cancel_appointment | 472 |
| expected=check_availability | 714 |
| expected=handoff_to_human | 649 |
| expected=reschedule_appointment | 778 |
| intent=availability | 714 |
| intent=book | 1005 |
| intent=cancel | 472 |
| intent=emergency | 71 |
| intent=faq | 382 |
| intent=faq_unknown | 257 |
| intent=human | 69 |
| intent=injection | 252 |
| intent=reschedule | 778 |
| noise=clean | 4000 |
| reference=present | 1250 |
| register=casual | 4000 |
| time_style=h12 | 2497 |

Missing pairs (first 50 of 676):

- correction=date x date_style=day_after_tomorrow
- correction=date x date_style=day_month
- correction=date x date_style=in_n_days
- correction=date x date_style=iso
- correction=date x date_style=month_day
- correction=date x date_style=tomorrow
- correction=date x date_style=weekday
- correction=date x duration_style=minutes
- correction=date x duration_style=unspecified
- correction=date x duration_style=words
- correction=date x edge=closed_day
- correction=date x edge=dst
- correction=date x edge=normal
- correction=date x edge=out_of_hours
- correction=date x edge=past
- correction=date x intent=availability
- correction=date x intent=book
- correction=date x intent=reschedule
- correction=date x noise=asr
- correction=date x noise=clean
- correction=date x noise=fillers
- correction=date x noise=stutter
- correction=date x noise=typo
- correction=date x reference=missing
- correction=date x reference=present
- correction=date x register=casual
- correction=date x register=code_switch
- correction=date x register=formal
- correction=date x register=terse
- correction=date x time_style=bare_ambiguous
- correction=date x time_style=h12
- correction=date x time_style=h12min
- correction=date x time_style=h24
- correction=date x time_style=missing
- correction=date x time_style=words
- correction=duration x date_style=day_after_tomorrow
- correction=duration x date_style=day_month
- correction=duration x date_style=in_n_days
- correction=duration x date_style=iso
- correction=duration x date_style=missing
- correction=duration x date_style=month_day
- correction=duration x date_style=next_weekday
- correction=duration x date_style=tomorrow
- correction=duration x date_style=weekday
- correction=duration x duration_style=minutes
- correction=duration x duration_style=words
- correction=duration x edge=closed_day
- correction=duration x edge=dst
- correction=duration x edge=normal
- correction=duration x edge=out_of_hours
