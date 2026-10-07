"""Full Telegram NFT gift catalog used by the UI."""

GIFT_CATALOG = [
    'Artisan Brick','Astral Shard','B-Day Candle','Berry Box','Big Year','Bling Binky',
    'Bonded Ring','Bow Tie','Bunny Muffin','Candy Cane','Chill Flame','Clover Pin',
    'Cookie Heart','Crystal Ball','Cupid Charm','Desk Calendar','Diamond Ring','Durov’s Cap',
    'Durov’s Glasses','Easter Egg','Electric Skull','Eternal Candle','Eternal Rose','Evil Eye',
    'Faith Amulet','Fine Pen','Flying Broom','Fresh Socks','Gem Signet','Genie Lamp',
    'Ginger Cookie','Hanging Star','Happy Brownie','Heart Locket','Heroic Helmet','Hex Pot',
    'Holiday Drink','Homemade Cake','Hypno Lollipop','Ice Cream','Input Key','Instant Ramen',
    'Ion Gem','Ionic Dryer','Jack-in-the-Box','Jelly Bunny','Jester Hat','Jingle Bells',
    'Jolly Chimp','Joyful Bundle','Khabib’s Papakha','Kissed Frog','Liberty Figure','Light Sword',
    'Lol Pop','Loot Bag','Love Candle','Love Potion','Low Rider','Lunar Snake','Lush Bouquet',
    'Mad Pumpkin','Magic Potion','Mighty Arm','Mini Oscar','Money Pot','Mood Pack','Moon Pendant',
    'Mousse Cake','Nail Bracelet','Neko Helmet','Party Sparkler','Perfume Bottle','Pet Snake',
    'Plush Pepe','Pool Float','Precious Peach','Pretty Posy','Rare Bird','Record Player',
    'Restless Jar','Sakura Flower','Santa Hat','Scared Cat','Sharp Tongue','Signet Ring',
    'Skull Flower','Sky Stilettos','Sleigh Bell','Snake Box','Snoop Cigar','Snoop Dogg',
    'Snow Globe','Snow Mittens','Spiced Wine','Spring Basket','Spy Agaric','Star Notepad',
    'Stellar Rocket','Surge Board','Swag Bag','Swiss Watch','Tama Gadget','Timeless Book',
    'Top Hat','Toy Bear','Trapped Heart','UFC Strike','Valentine Box','Vice Cream','Victory Medal',
    'Vintage Cigar','Voodoo Doll','Westside Sign','Whip Cupcake','Winter Wreath','Witch Hat',
    'Xmas Stocking'
]

# API-friendly names. Peek historically expects CamelCase without spaces.
def api_name(title: str) -> str:
    return ''.join(part[:1].upper() + part[1:] for part in title.replace('’', '').replace("'", '').replace('-', ' ').split())

GIFT_MODELS = [api_name(x) for x in GIFT_CATALOG]
GIFT_BY_API = {api_name(x): x for x in GIFT_CATALOG}
GIFT_PAGE_SIZE = 12
