# BuyWise rug retrieval evaluation

Snapshot 0b5986990bef8956ad13. 25 indexed listings; 24 eligible designs. Model openai/clip-vit-base-patch32 at 3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268.

Both methods used one common candidate per design. Reference designs were excluded. Image queries used stored CLIP vectors and cosine similarity. Title queries ranked by distinct shared normalized, stemmed terms. Ties used stable catalog keys. The assessor judged the shuffled union of both top fives with method and rank hidden. Each precision denominator is five, including missing positions.

| Reference | Title P@5 | Image P@5 | Shop alternatives above cutoff |
|---|---:|---:|---:|
| ONSEVIG Rug, low pile - multicolour 160x235 cm | 0.20 | 0.40 | 3 |
| VÄNDPLATS Rug, low pile - white/black dotted 133x195 cm | 0.20 | 0.60 | 0 |
| STOCKHOLM 2025 Rug, flatwoven - black/white/light green handwoven 170x240 cm | 0.60 | 0.60 | 0 |
| TELEGRAFLINJE Rug, flatwoven - yellow-beige 170x240 cm | 0.40 | 0.60 | 0 |
| Helena Rug - 200x290 cm | 0.20 | 0.20 | 5 |
| Veluna Printed Rug - 200x290 cm | 0.40 | 0.20 | 7 |
| Jolie Rug - 200x290 cm | 0.00 | 0.20 | 0 |
| Noctis Calista Woollen Handtufted Rug - 300x400 cm | 0.20 | 0.20 | 1 |

Mean Precision@5: title 0.275, image 0.375.
Empty-result frequency at cosine > 0.9: 4/8 (50.0%).

## Judged examples

- ONSEVIG Rug, low pile - multicolour 160x235 cm / title: TOFTEBJERG Rug, low pile - multicolour 160x235 cm [irrelevant], BRÖNDEN Rug, low pile - handmade multicolour/red 170x240 cm [irrelevant], ELSYSTEM Rug, low pile - multicolour 133x195 cm [irrelevant], LOKALTÅG Rug, low pile - beige/grey 160x230 cm [irrelevant], JÄRNVÄG Rug, low pile - ornament pattern pink/beige 160x230 cm [relevant]
- ONSEVIG Rug, low pile - multicolour 160x235 cm / image: JÄRNVÄG Rug, low pile - ornament pattern pink/beige 160x230 cm [relevant], LOKALTÅG Rug, low pile - beige/grey 160x230 cm [irrelevant], TOFTEBJERG Rug, low pile - multicolour 160x235 cm [irrelevant], Aris Rug - 200x290 cm [irrelevant], Dinah Printed Rug - 200x290 cm [relevant]
- VÄNDPLATS Rug, low pile - white/black dotted 133x195 cm / title: ELSYSTEM Rug, low pile - multicolour 133x195 cm [irrelevant], TOFTEBJERG Rug, low pile - multicolour 160x235 cm [irrelevant], BRÖNDEN Rug, low pile - handmade multicolour/red 170x240 cm [irrelevant], STOCKHOLM 2025 Rug, flatwoven - black/white/light green handwoven 170x240 cm [relevant], LOKALTÅG Rug, low pile - beige/grey 160x230 cm [irrelevant]
- VÄNDPLATS Rug, low pile - white/black dotted 133x195 cm / image: STOCKHOLM 2025 Rug, flatwoven - black/white/light green handwoven 170x240 cm [relevant], KONSTRUNDA Rug - white/black 80x160 cm [relevant], TELEGRAFLINJE Rug, flatwoven - yellow-beige 170x240 cm [relevant], TVERSTED Rug, low pile - beige 200x300 cm [irrelevant], BRÖNDEN Rug, low pile - handmade multicolour/red 170x240 cm [irrelevant]
- STOCKHOLM 2025 Rug, flatwoven - black/white/light green handwoven 170x240 cm / title: VÄNDPLATS Rug, low pile - white/black dotted 133x195 cm [relevant], KONSTRUNDA Rug - white/black 80x160 cm [relevant], TELEGRAFLINJE Rug, flatwoven - yellow-beige 170x240 cm [relevant], BRÖNDEN Rug, low pile - handmade multicolour/red 170x240 cm [irrelevant], Madison Floor Rug - 160x230 cm [irrelevant]
- STOCKHOLM 2025 Rug, flatwoven - black/white/light green handwoven 170x240 cm / image: KONSTRUNDA Rug - white/black 80x160 cm [relevant], ELSYSTEM Rug, low pile - multicolour 133x195 cm [irrelevant], VÄNDPLATS Rug, low pile - white/black dotted 133x195 cm [relevant], TELEGRAFLINJE Rug, flatwoven - yellow-beige 170x240 cm [relevant], BRÖNDEN Rug, low pile - handmade multicolour/red 170x240 cm [irrelevant]
- TELEGRAFLINJE Rug, flatwoven - yellow-beige 170x240 cm / title: STOCKHOLM 2025 Rug, flatwoven - black/white/light green handwoven 170x240 cm [relevant], BRÖNDEN Rug, low pile - handmade multicolour/red 170x240 cm [irrelevant], LOKALTÅG Rug, low pile - beige/grey 160x230 cm [irrelevant], JÄRNVÄG Rug, low pile - ornament pattern pink/beige 160x230 cm [irrelevant], TVERSTED Rug, low pile - beige 200x300 cm [relevant]
- TELEGRAFLINJE Rug, flatwoven - yellow-beige 170x240 cm / image: Neisha Chalon Rug - 240x360 cm [irrelevant], STOCKHOLM 2025 Rug, flatwoven - black/white/light green handwoven 170x240 cm [relevant], Madison Floor Rug - 160x230 cm [irrelevant], TVERSTED Rug, low pile - beige 200x300 cm [relevant], VÄNDPLATS Rug, low pile - white/black dotted 133x195 cm [relevant]
- Helena Rug - 200x290 cm / title: Dake Rug - 200x290 cm [irrelevant], Mason Rug - 200x290 cm [irrelevant], Jolie Rug - 200x290 cm [irrelevant], Dinah Printed Rug - 200x290 cm [relevant], Veluna Printed Rug - 200x290 cm [irrelevant]
- Helena Rug - 200x290 cm / image: Genelle Printed Rug - 160x230 cm [irrelevant], Veluna Printed Rug - 200x290 cm [irrelevant], Zannah Rug - 240X360 cm [irrelevant], Aris Rug - 200x290 cm [irrelevant], Dinah Printed Rug - 200x290 cm [relevant]
- Veluna Printed Rug - 200x290 cm / title: Dinah Printed Rug - 200x290 cm [relevant], Helena Rug - 200x290 cm [irrelevant], Dake Rug - 200x290 cm [irrelevant], Mason Rug - 200x290 cm [relevant], Genelle Printed Rug - 160x230 cm [irrelevant]
- Veluna Printed Rug - 200x290 cm / image: Genelle Printed Rug - 160x230 cm [irrelevant], Aris Rug - 200x290 cm [irrelevant], Zannah Rug - 240X360 cm [relevant], Helena Rug - 200x290 cm [irrelevant], Neraya Rug - 200x290 cm [irrelevant]
- Jolie Rug - 200x290 cm / title: Helena Rug - 200x290 cm [irrelevant], Dake Rug - 200x290 cm [irrelevant], Mason Rug - 200x290 cm [irrelevant], Dinah Printed Rug - 200x290 cm [irrelevant], Veluna Printed Rug - 200x290 cm [irrelevant]
- Jolie Rug - 200x290 cm / image: Dinah Printed Rug - 200x290 cm [irrelevant], Madison Floor Rug - 160x230 cm [irrelevant], Neisha Chalon Rug - 240x360 cm [relevant], Zannah Rug - 240X360 cm [irrelevant], Genelle Printed Rug - 160x230 cm [irrelevant]
- Noctis Calista Woollen Handtufted Rug - 300x400 cm / title: Madison Floor Rug - 160x230 cm [irrelevant], Neisha Chalon Rug - 240x360 cm [relevant], Helena Rug - 200x290 cm [irrelevant], Dake Rug - 200x290 cm [irrelevant], Mason Rug - 200x290 cm [irrelevant]
- Noctis Calista Woollen Handtufted Rug - 300x400 cm / image: Neisha Chalon Rug - 240x360 cm [relevant], Madison Floor Rug - 160x230 cm [irrelevant], Neraya Rug - 200x290 cm [irrelevant], Veluna Printed Rug - 200x290 cm [irrelevant], Zannah Rug - 240X360 cm [irrelevant]

This small, single-assessor pilot evaluates the selected references only. Photographic staging, title conventions and the retailer mix may influence rankings.