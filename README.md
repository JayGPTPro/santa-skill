# The Santa Skill

Turns the lifestyle images of your Amazon listings into Christmas versions, for one product or a whole list at once. The product, the text and the people stay the same. The main image, white-background shots and infographics are never touched.

## Install

    npx skills add JayGPTPro/santa-skill -g

## Run

One product:

    /santa-skill B0GYY18GYC

Many products: paste the product links or ASINs after the command.

    /santa-skill <your product links>

You get a folder for every ASIN with its Christmas images and a before/after page, and one page that shows them all.

## Needs

`OPENAI_API_KEY` in your environment (gpt-image-2 needs a verified OpenAI org). About 9 cents an image. Optional, for the check that the product kept its size: `pip install opencv-python-headless`.

More: https://jaygptpro.com/claude/santa-skill.html
