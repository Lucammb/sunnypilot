# lightpilot

**lightpilot** è un fork personale di [sunnypilot](https://github.com/sunnypilot/sunnypilot) (a sua volta fork di [openpilot di comma.ai](https://github.com/commaai/openpilot)) per una Tesla Model 3 HW3 con comma / Konik A1m. Non è affiliato a sunnypilot né a comma.ai. È software sperimentale: si usa **sempre supervisionati**, con le mani sul volante e gli occhi sulla strada.

## Cosa aggiunge rispetto a sunnypilot

- **Navigazione assistita**: destinazione e percorso (Mapbox) con mappa sempre visibile a metà schermo.
- **Velocità prima di rotonde, svolte e stop**: il limite scende dolcemente a 30 km/h (25 per gli stop) prima della zona. Frena soltanto, non accelera mai; l'acceleratore del guidatore ha sempre la precedenza.
- **Svolta dalla rotta**: in sperimentale, suggerisce la direzione della svolta o dell'uscita dalla rotonda come se fosse acceso l'indicatore. Il blinker del guidatore ha sempre la priorità. **Nessun cambio di corsia automatico, mai.**
- **Destinazione dal telefono**: pagina web per impostare/annullare la destinazione senza SSH (`openpilot/lightpilot/phone`).
- **Aggiornamenti controllati**: niente aggiornamenti automatici da comma; la Konik si aggiorna solo dal puntatore `STABLE` di questo repository, che viene spostato solo dopo i test.
- **Diagnostica**: `/data/lightpilot/turn.log` registra cosa decide l'aiuto alla svolta.

I dettagli e le impostazioni sono in [LIGHTPILOT.md](LIGHTPILOT.md). Il codice nostro sta in `openpilot/lightpilot/`.

## Aggiornamento da sunnypilot

Ogni lunedì un controllo automatico cerca nuove release stabili di sunnypilot, le integra, esegue i test e, solo se tutto passa e non tocca parti critiche di sicurezza, aggiorna `STABLE`.

> **Avviso**: software sperimentale, senza alcuna garanzia, solo per ricerca. Chi lo usa è responsabile del rispetto delle leggi e dei regolamenti locali. Vedi la licenza sotto.

---

# sunnypilot (testo originale)

![](https://user-images.githubusercontent.com/47793918/233812617-beab2e71-57b9-479e-8bff-c3931347ca40.png)

## 🌞 What is sunnypilot?
[sunnypilot](https://github.com/sunnyhaibin/sunnypilot) is a fork of comma.ai's openpilot, an open source driver assistance system. sunnypilot offers the user a unique driving experience for over 300+ supported car makes and models with modified behaviors of driving assist engagements. sunnypilot complies with comma.ai's safety rules as accurately as possible.

## 💭 Join our Community Forum
Join the official sunnypilot community forum to stay up to date with all the latest features and be a part of shaping the future of sunnypilot!
* https://community.sunnypilot.ai/

## Documentation
https://docs.sunnypilot.ai/ is your one stop shop for everything from features to installation to FAQ about the sunnypilot

## 🚘 Running on a dedicated device in a car
First, check out this list of items you'll need to [get started](https://community.sunnypilot.ai/t/getting-started-using-sunnypilot-in-your-supported-car/251).

## Installation
Next, refer to the sunnypilot community forum for [installation instructions](https://community.sunnypilot.ai/t/read-before-installing-sunnypilot/254), as well as a complete list of [Recommended Branch Installations](https://community.sunnypilot.ai/t/recommended-branch-installations/235).

## 🎆 Pull Requests
We welcome both pull requests and issues on GitHub. Bug fixes are encouraged.

Pull requests should be against the most current `master` branch.

## 📊 User Data

By default, sunnypilot uploads the driving data to comma servers. You can also access your data through [comma connect](https://connect.comma.ai/).

sunnypilot is open source software. The user is free to disable data collection if they wish to do so.

sunnypilot logs the road-facing camera, CAN, GPS, IMU, magnetometer, thermal sensors, crashes, and operating system logs.
The driver-facing camera and microphone are only logged if you explicitly opt-in in settings.

By using this software, you understand that use of this software or its related services will generate certain types of user data, which may be logged and stored at the sole discretion of comma. By accepting this agreement, you grant an irrevocable, perpetual, worldwide right to comma for the use of this data.

## Licensing

sunnypilot is released under the [MIT License](LICENSE). This repository includes original work as well as significant portions of code derived from [openpilot by comma.ai](https://github.com/commaai/openpilot), which is also released under the MIT license with additional disclaimers.

The original openpilot license notice, including comma.ai’s indemnification and alpha software disclaimer, is reproduced below as required:

> openpilot is released under the MIT license. Some parts of the software are released under other licenses as specified.
>
> Any user of this software shall indemnify and hold harmless Comma.ai, Inc. and its directors, officers, employees, agents, stockholders, affiliates, subcontractors and customers from and against all allegations, claims, actions, suits, demands, damages, liabilities, obligations, losses, settlements, judgments, costs and expenses (including without limitation attorneys’ fees and costs) which arise out of, relate to or result from any use of this software by user.
>
> **THIS IS ALPHA QUALITY SOFTWARE FOR RESEARCH PURPOSES ONLY. THIS IS NOT A PRODUCT.
> YOU ARE RESPONSIBLE FOR COMPLYING WITH LOCAL LAWS AND REGULATIONS.
> NO WARRANTY EXPRESSED OR IMPLIED.**

For full license terms, please see the [`LICENSE`](LICENSE) file.

## 💰 Support sunnypilot
If you find any of the features useful, consider becoming a [sponsor on GitHub](https://github.com/sponsors/sunnyhaibin) to support future feature development and improvements.


By becoming a sponsor, you will gain access to exclusive content, early access to new features, and the opportunity to directly influence the project's development.


<h3>GitHub Sponsor</h3>

<a href="https://github.com/sponsors/sunnyhaibin">
  <img src="https://user-images.githubusercontent.com/47793918/244135584-9800acbd-69fd-4b2b-bec9-e5fa2d85c817.png" alt="Become a Sponsor" width="300" style="max-width: 100%; height: auto;">
</a>
<br>

<h3>PayPal</h3>

<a href="https://paypal.me/sunnyhaibin0850" target="_blank">
<img src="https://www.paypalobjects.com/en_US/i/btn/btn_donateCC_LG.gif" alt="PayPal this" title="PayPal - The safer, easier way to pay online!" border="0" />
</a>
<br></br>

Your continuous love and support are greatly appreciated! Enjoy 🥰

<span>-</span> Jason, Founder of sunnypilot
