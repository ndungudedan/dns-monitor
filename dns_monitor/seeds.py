"""
Bitcoin DNS seed lists from chainparams.cpp.
https://github.com/bitcoin/bitcoin/blob/master/src/kernel/chainparams.cpp
"""

SEEDS = {
    "mainnet": [
        "seed.bitcoin.sipa.be",
        "dnsseed.bluematt.me",
        "seed.bitcoin.jonasschnelli.ch",
        "seed.btc.petertodd.net",
        "seed.bitcoin.sprovoost.nl",
        "dnsseed.emzy.de",
        "seed.bitcoin.wiz.biz",
        "seed.mainnet.achownodes.xyz",
    ],
    "testnet3": [
        "testnet-seed.bitcoin.jonasschnelli.ch",
        "seed.tbtc.petertodd.net",
        "seed.testnet.bitcoin.sprovoost.nl",
        "testnet-seed.bluematt.me",
        "seed.testnet.achownodes.xyz",
    ],
    "testnet4": [
        "seed.testnet4.bitcoin.sprovoost.nl",
        "seed.testnet4.wiz.biz",
    ],
    "signet": [
        "seed.signet.bitcoin.sprovoost.nl",
        "seed.signet.achownodes.xyz",
    ],
}

DEFAULT_PORT = {
    "mainnet": 8333,
    "testnet3": 18333,
    "testnet4": 48333,
    "signet": 38333,
}
