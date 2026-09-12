# Messergebnisse

Median aus mehreren Durchläufen, Millisekunden.

## Vollaufbau bis gezeichnet

| Variante | 2 000 Zeilen | 20 000 Zeilen | 100 000 Zeilen |
|---|---|---|---|
| shared-core | 0.9 | 9.5 | 49.2 |
| qt-offscreen | 63.9 | 79.6 | 118.1 |
| qt-x11 | 71.8 | 78.4 | 144.4 |
| tk-x11 | 36.5 | 485.7 | 2864.2 |
| web-server | 3.9 | 10.5 | 53.1 |

## Vollaufbau ohne erzwungenes Neuzeichnen

| Variante | 2 000 Zeilen | 20 000 Zeilen | 100 000 Zeilen |
|---|---|---|---|
| shared-core | 0.9 | 9.5 | 49.2 |
| qt-offscreen | 1.1 | 10.2 | 50.3 |
| qt-x11 | 1.1 | 10.1 | 48.7 |
| tk-x11 | 25.0 | 263.1 | 1251.6 |
| web-server | 3.9 | 10.5 | 53.1 |
