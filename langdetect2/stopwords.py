"""拉丁/西里尔语言的常用功能词表。

用于同脚本内区分语言（latin、cyrillic 无法仅靠字符范围确定语言）。
词表刻意保持小而高频，命中数作为打分依据，不追求完备。
"""

STOPWORDS = {
    "en": [
        "the", "be", "to", "of", "and", "in", "that", "have", "it", "for",
        "not", "on", "with", "he", "as", "you", "do", "at", "this", "but",
        "his", "by", "from", "they", "we", "say", "her", "she", "or", "an",
        "will", "my", "one", "all", "would", "there", "their", "what", "so",
        "up", "out", "if", "about", "who", "get", "which", "go", "me", "when",
        "make", "can", "like", "time", "no", "just", "him", "know", "take",
        "people", "into", "year", "your", "good", "some", "could", "them",
        "see", "other", "than", "then", "now", "look", "only", "come", "its",
        "over", "think", "also", "back", "after", "use", "two", "how", "our",
        "work", "first", "well", "way", "even", "new", "want", "because",
        "any", "these", "give", "day", "most", "us",
        "is", "are", "was", "were", "been", "has", "had", "did", "does",
    ],
    "de": [
        "der", "die", "und", "in", "den", "von", "zu", "das", "mit", "sich",
        "des", "auf", "für", "ist", "im", "dem", "nicht", "ein", "eine", "als",
        "auch", "es", "an", "er", "aus", "wird", "werden", "bei", "nach",
        "sind", "oder", "um", "wie", "noch", "nur", "einem", "schon", "ihre",
        "seiner", "ihrem", "kann", "dieser", "diese", "diesem", "aber",
        "einen", "dann", "wir", "ihr", "habe", "hat", "war", "waren", "vom",
        "zum", "zur", "beim", "haben", "hatte", "hatten", "bin", "bist",
        "soll", "sollen", "muss", "müssen",
    ],
    "fr": [
        "le", "la", "les", "un", "une", "des", "du", "de", "et", "à", "en",
        "au", "aux", "ce", "ces", "dans", "pour", "par", "sur", "avec", "sans",
        "sous", "entre", "vers", "chez", "depuis", "pendant", "dès", "afin",
        "durant", "qui", "que", "quoi", "dont", "où", "je", "tu", "il", "elle",
        "nous", "vous", "ils", "elles", "me", "te", "se", "mon", "ma", "mes",
        "ton", "ta", "tes", "son", "sa", "ses", "notre", "votre", "leur",
        "leurs", "être", "avoir", "faire", "aller", "dire", "pouvoir", "voir",
        "venir", "est", "sont", "était", "étaient", "suis", "es", "as",
        "avons", "avez", "ont", "ai", "eu", "fus",
    ],
    "es": [
        "el", "los", "las", "uno", "unos", "unas", "del", "con", "por",
        "para", "lo", "su", "sus", "le", "les", "mi", "mis", "tu", "tus",
        "nuestro", "vuestro", "cual", "quienes", "cuyo", "cuando", "donde",
        "como", "porque", "aunque", "sino", "mas", "pero", "sí", "ser",
        "estar", "haber", "tener", "hacer", "decir", "dar", "poder", "saber",
        "querer", "llegar", "pasar", "deber", "eso", "esto", "esta", "este",
        "esas", "esos", "aquí", "allí", "allá", "ahora", "antes", "después",
        "muy", "más", "menos",
    ],
    "it": [
        "il", "lo", "i", "gli", "uno", "una", "di", "a", "da", "in", "con",
        "su", "per", "tra", "fra", "del", "dello", "della", "nel", "nella",
        "dal", "dalla", "al", "alla", "sul", "sulla", "che", "chi", "cui",
        "quale", "quanto", "come", "dove", "quando", "perché", "mentre", "se",
        "poiché", "io", "tu", "lui", "lei", "noi", "voi", "loro", "essi", "mi",
        "ti", "si", "ci", "vi", "essere", "avere", "fare", "dire", "andare",
        "vedere", "sapere", "potere", "volere", "dovere", "questo", "quello",
        "questi", "quegli", "qui", "qua", "lì", "là", "oggi", "domani",
        "sempre", "anche",
    ],
    "pt": [
        "uma", "umas", "do", "da", "dos", "das", "em", "no", "na", "nas", "é",
        "são", "com", "para", "por", "ao", "aos", "dum", "duma", "que", "quem",
        "qual", "quais", "cujo", "onde", "quando", "como", "porque", "embora",
        "pois", "mas", "porém", "sim", "não", "ser", "estar", "haver", "ter",
        "fazer", "ir", "dizer", "ver", "dar", "poder", "saber", "querer",
        "chegar", "passar", "dever", "isso", "esta", "este", "essas", "esses",
        "aqui", "ali", "agora", "antes", "depois", "muito", "mais", "menos",
        "também", "já", "ainda",
    ],
    "nl": [
        "het", "een", "van", "en", "in", "is", "dat", "op", "te", "zijn",
        "voor", "met", "aan", "niet", "ook", "maar", "om", "dan", "nog", "uit",
        "bij", "wij", "ze", "hij", "hen", "haar", "mijn", "jouw", "onze", "hun",
        "wie", "wat", "welke", "waarvan", "waarbij", "waardoor", "wanneer",
        "waarom", "hoe", "worden", "hebben", "kunnen", "zullen", "mogen",
        "moeten", "dit", "die", "deze", "diegene", "degene", "hier", "daar",
        "ergens", "nergens", "over", "onder", "tussen", "achter", "naast",
        "sedert", "vanaf",
    ],
    "tr": [
        "bir", "ve", "bu", "için", "ile", "olarak", "daha", "çok", "ama",
        "gibi", "kadar", "en", "son", "göre", "ne", "var", "yok", "zaman",
        "iki", "onu", "ben", "sen", "siz", "biz", "o", "şu", "şöyle", "böyle",
        "her", "hiç", "bazı", "veya", "ise", "mi", "mı", "mu", "mü", "ki",
        "diye", "eğer", "çünkü", "önce", "sonra", "tarafından", "edilmek",
        "olunmak", "görmek", "gelmek", "vermek", "gitmek", "almak", "bilmek",
        "istemek", "söylemek",
    ],
    "ru": [
        "и", "в", "во", "не", "что", "он", "на", "я", "с", "со", "как", "а",
        "то", "все", "она", "так", "его", "но", "да", "ты", "к", "у", "же",
        "вы", "за", "бы", "по", "только", "ее", "мне", "было", "вот", "от",
        "меня", "еще", "нет", "о", "из", "ему", "теперь", "когда", "даже",
        "ну", "вдруг", "ли", "если", "или", "ни", "быть", "был", "него", "до",
        "вас", "ничего", "лучше", "чуть", "того", "чтобы", "этот", "этом",
        "эту", "этих", "этими", "нами", "ними", "ими", "были", "есть",
        "это", "через", "собаку", "для",
    ],
    "uk": [
        "і", "в", "у", "на", "з", "до", "не", "що", "це", "як", "та", "його",
        "але", "я", "ми", "ви", "вони", "вона", "він", "мене", "тебе", "себе",
        "нас", "вас", "їх", "мені", "тобі", "собі", "нам", "вам", "їм", "ним",
        "нею", "був", "була", "були", "було", "є", "буде", "будуть", "який",
        "яка", "яке", "які", "чи", "якщо", "щоб", "коли", "поки", "бо", "теж",
        "також", "вже", "ще", "дуже", "більш", "менш", "ніколи", "завжди",
        "зараз", "потім", "тут", "там", "отут",
    ],
}

# 按脚本划分语言，避免 latin/cyrillic 互相串分
_LATIN_LANGS = ("en", "de", "fr", "es", "it", "pt", "nl", "tr")
_CYRILLIC_LANGS = ("ru", "uk")


def tokenize(text):
    """切出拉丁/西里尔字母词，CJK 文本不会被切到。"""
    import re
    return re.findall(r"[A-Za-zÀ-ɏЀ-ӿ]+", text)


def score(tokens):
    """返回 {语言: 命中词数}，按命中数降序排列。"""
    scores = {}
    for token in tokens:
        word = token.lower()
        for lang, words in STOPWORDS.items():
            if word in words:
                scores[lang] = scores.get(lang, 0) + 1
    return dict(sorted(scores.items(), key=lambda kv: kv[1], reverse=True))


def latin_scores(tokens):
    return {lang: n for lang, n in score(tokens).items() if lang in _LATIN_LANGS}


def cyrillic_scores(tokens):
    return {lang: n for lang, n in score(tokens).items()
            if lang in _CYRILLIC_LANGS}
