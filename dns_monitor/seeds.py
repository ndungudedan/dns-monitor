"""
Bitcoin DNS seed lists from chainparams.cpp.
https://github.com/bitcoin/bitcoin/blob/master/src/kernel/chainparams.cpp
"""

SEEDS = {
    "mainnet": [
        "seed.bitcoin.sipa.be",
        "dnsseed.bluematt.me",
        "dnsseed.bitcoin.dashjr.org",
        "seed.bitcoinstats.com",
        "seed.bitcoin.jonasschnelli.ch",
        "seed.btc.petertodd.net",
        "seed.bitcoin.sprovoost.nl",
        "dnsseed.emzy.de",
        "seed.bitcoin.wiz.biz",
    ],
    "testnet3": [
        "testnet-seed.bitcoin.jonasschnelli.ch",
        "seed.tbtc.petertodd.net",
        "seed.testnet.bitcoin.sprovoost.nl",
        "testnet-seed.bluematt.me",
    ],
    "signet": [
        "seed.signet.bitcoin.sprovoost.nl",
    ],
}

DEFAULT_PORT = {
    "mainnet": 8333,
    "testnet3": 18333,
    "signet": 38333,
}
