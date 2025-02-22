from tensorflow.keras.models import Sequential, Model
from tensorflow.keras.layers import LSTM, Bidirectional, Conv1D, Flatten, Lambda, RepeatVector
from tensorflow.keras.layers import Dense, Input, Concatenate, Masking, MaxPooling1D
from tensorflow.keras.layers import TimeDistributed, Dropout, BatchNormalization, Activation
from tensorflow.keras.utils import to_categorical
import tensorflow_model_optimization as tfmot
from tensorflow.keras.optimizers import Adam
import tensorflow.keras as keras
import multiprocessing
import numpy as np
import tensorflow as tf

from tensorflow import lite as tflite
from keras.preprocessing.sequence import pad_sequences
from tensorflow.keras.callbacks import EarlyStopping, LearningRateScheduler
import time
import pyxmorphy
from pyxmorphy import UniSPTag, UniMorphTag
import fasttext
from enum import Enum

SPEECH_PARTS = [
    'X',
    'ADJ',
    'ADV',
    'INTJ',
    'NOUN',
    'PROPN',
    'VERB',
    'ADP',
    'AUX',
    'CONJ',
    'SCONJ',
    'DET',
    'NUM',
    'PART',
    'PRON',
    'PUNCT',
    'H',
    'R',
    'Q',
    'SYM',
]

SPEECH_PART_MAPPING = {str(s): num for num, s in enumerate(SPEECH_PARTS)}

MASK_VALUE = 0.0


def build_speech_part_array(sp):
    output = [0. for _ in range(len(SPEECH_PARTS))]
    output[SPEECH_PART_MAPPING[str(sp)]] = 1.
    return output


PARTS_MAPPING = {
    'UNKN': 0,
    'PREF': 1,
    'ROOT': 2,
    'SUFF': 3,
    'END': 4,
    'LINK': 5,
    'HYPH': 6,
    'POSTFIX': 7,
    'B-SUFF': 8,
    'B-PREF': 9,
    'B-ROOT': 10,
    'NUMB': 11,
}

LETTERS = {
    'о': 1,
    'е': 2,
    'а': 3,
    'и': 4,
    'н': 5,
    'т': 6,
    'с': 7,
    'р': 8,
    'в': 9,
    'л': 10,
    'к': 11,
    'м': 12,
    'д': 13,
    'п': 14,
    'у': 15,
    'я': 16,
    'ы': 17,
    'ь': 18,
    'г': 19,
    'з': 20,
    'б': 21,
    'ч': 22,
    'й': 23,
    'х': 24,
    'ж': 25,
    'ш': 26,
    'ю': 27,
    'ц': 28,
    'щ': 29,
    'э': 30,
    'ф': 31,
    'ъ': 32,
    'ё': 33,
    '-': 34,
}


VOWELS = {
    'а', 'и', 'е', 'ё', 'о', 'у', 'ы', 'э', 'ю', 'я'
}

VOICED_CONSONANTS = {
    'б', 'в', 'г', 'д', 'з', 'ж', 'л', 'м', 'н', 'р', 'й',
}


class MorphemeLabel(Enum):
    UNKN = 'UNKN'
    PREF = 'PREF'
    ROOT = 'ROOT'
    SUFF = 'SUFF'
    END = 'END'
    LINK = 'LINK'
    HYPH = 'HYPH'
    POSTFIX = 'POSTFIX'
    NUMB = 'NUMB'
    NONE = None


class Morpheme(object):
    def __init__(self, part_text, label, begin_pos):
        self.part_text = part_text
        self.length = len(part_text)
        self.begin_pos = begin_pos
        self.label = label
        self.end_pos = self.begin_pos + self.length

    def __len__(self):
        return self.length

    def get_labels(self):
        if self.length == 1:
            return ['S-' + self.label.value]
        result = ['B-' + self.label.value]
        result += ['M-' + self.label.value for _ in self.part_text[1:-1]]
        result += ['E-' + self.label.value]
        return result

    def get_simple_labels(self):
        if (self.label == MorphemeLabel.SUFF or self.label == MorphemeLabel.PREF or self.label == MorphemeLabel.ROOT):

            result = ['B-' + self.label.value]
            if self.length > 1:
                result += [self.label.value for _ in self.part_text[1:]]
            return result
        else:
            return [self.label.value] * self.length

    def __str__(self):
        return self.part_text + ':' + self.label.value

    @property
    def unlabeled(self):
        return not self.label.value


class Word(object):
    def __init__(self, morphemes=[], speech_part='X', trim_length=None):
        self.morphemes = morphemes
        self.sp = speech_part
        self.trim_length = trim_length

    def append_morpheme(self, morpheme):
        self.morphemes.append(morpheme)

    def get_word(self):
        if self.trim_length is None:
            return ''.join([morpheme.part_text for morpheme in self.morphemes])
        return ''.join([morpheme.part_text for morpheme in self.morphemes])[:self.trim_length]

    def get_speech_part(self):
        return self.sp

    def get_labels(self):
        result = []
        for morpheme in self.morphemes:
            result += morpheme.get_labels()
        if self.trim_length is None:
            return result
        return result[:self.trim_length]

    def get_simple_labels(self):
        result = []
        for morpheme in self.morphemes:
            result += morpheme.get_simple_labels()
        if self.trim_length is None:
            return result
        return result[:self.trim_length]

    def __str__(self):
        return '/'.join([str(morpheme) for morpheme in self.morphemes])

    def __len__(self):
        if self.trim_length is None:
            return sum(len(m) for m in self.morphemes)

        return min(sum(len(m) for m in self.morphemes), self.trim_length)

    @property
    def unlabeled(self):
        return all(p.unlabeled for p in self.morphemes)


def parse_morpheme(str_repr, position):
    text, label = str_repr.split(':')
    return Morpheme(text, MorphemeLabel[label], position)


def parse_word(wordform, parse, sp, trim_length):
    if ':' in wordform or '/' in wordform:
        return Word([Morpheme(wordform, MorphemeLabel['UNKN'], 0)], sp, trim_length)

    parts = parse.split('/')
    morphemes = []
    global_index = 0
    for part in parts:
        morphemes.append(parse_morpheme(part, global_index))
        global_index += len(part)
    return Word(morphemes, sp, trim_length)


def measure_quality(predicted_targets, targets, words, verbose=False):
    TP, FP, FN, equal, total = 0, 0, 0, 0, 0
    SE = ['{}-{}'.format(x, y) for x in "SE" for y in ["ROOT", "PREF", "SUFF", "END", "LINK", "UNKN", "HYPH", "NUMB"]]
    corr_words = 0
    for corr, pred, word in zip(targets, predicted_targets, words):
        corr_len = len(corr)
        pred_len = len(pred)
        boundaries = [i for i in range(corr_len) if corr[i] in SE]
        pred_boundaries = [i for i in range(pred_len) if pred[i] in SE]
        common = [x for x in boundaries if x in pred_boundaries]
        TP += len(common)
        FN += len(boundaries) - len(common)
        FP += len(pred_boundaries) - len(common)
        equal += sum(int(x == y) for x, y in zip(corr, pred))
        total += len(corr)
        corr_words += (corr == pred)
        if corr != pred and verbose:
            print("Error in word '{}':\n correct:".format(word), corr, '\n!=\n wrong:', pred)

    metrics = ["Precision", "Recall", "F1", "Accuracy", "Word accuracy"]
    results = [TP / (TP+FP), TP / (TP+FN), TP / (TP + 0.5*(FP+FN)),
               equal / total, corr_words / len(targets)]
    return list(zip(metrics, results))

def _transform_classification(parse):
    parts = []
    current_part = [parse[0]]
    for num, letter in enumerate(parse[1:]):
        index = num + 1
        if letter == 'SUFF' and parse[index - 1] == 'B-SUFF':
            current_part.append(letter)
        elif letter == 'PREF' and parse[index - 1] == 'B-PREF':
            current_part.append(letter)
        elif letter == 'ROOT' and parse[index - 1] == 'B-ROOT':
            current_part.append(letter)
        elif letter != parse[index - 1] or letter.startswith('B-'):
            parts.append(current_part)
            current_part = [letter]
        else:
            current_part.append(letter)
    if current_part:
        parts.append(current_part)

    for part in parts:
        if part[0] == 'B-PREF':
            part[0] = 'PREF'
        if part[0] == 'B-SUFF':
            part[0] = 'SUFF'
        if part[0] == 'B-ROOT':
            part[0] = 'ROOT'
        if len(part) == 1:
            part[0] = 'S-' + part[0]
        else:
            part[0] = 'B-' + part[0]
            part[-1] = 'E-' + part[-1]
            for num, letter in enumerate(part[1:-1]):
                part[num+1] = 'M-' + letter
    result = []
    for part in parts:
        result += part
    return result

SPEECH_PARTS = [
    UniSPTag.X,
    UniSPTag.ADJ,
    UniSPTag.ADV,
    UniSPTag.INTJ,
    UniSPTag.NOUN,
    UniSPTag.PROPN,
    UniSPTag.VERB,
    UniSPTag.ADP,
    UniSPTag.AUX,
    UniSPTag.CONJ,
    UniSPTag.SCONJ,
    UniSPTag.DET,
    UniSPTag.NUM,
    UniSPTag.PART,
    UniSPTag.PRON,
    UniSPTag.PUNCT,
    UniSPTag.H,
    UniSPTag.R,
    UniSPTag.Q,
    UniSPTag.SYM,
]

EMBED_SIZE = 50

CASE_TAGS = [
    UniMorphTag.UNKN,
    UniMorphTag.Ins,
    UniMorphTag.Acc,
    UniMorphTag.Nom,
    UniMorphTag.Dat,
    UniMorphTag.Gen,
    UniMorphTag.Loc,
    UniMorphTag.Voc,
]

NUMBER_TAGS = [
    UniMorphTag.UNKN,
    UniMorphTag.Sing,
    UniMorphTag.Plur,
]

GENDER_TAGS = [
    UniMorphTag.UNKN,
    UniMorphTag.Masc,
    UniMorphTag.Fem,
    UniMorphTag.Neut,
]

TENSE_TAGS = [
    UniMorphTag.UNKN,
    UniMorphTag.Fut,
    UniMorphTag.Past,
    UniMorphTag.Pres,
    UniMorphTag.Notpast,
]

ANIMACY_TAGS = [
    UniMorphTag.UNKN,
    UniMorphTag.Anim,
    UniMorphTag.Inan,
]

embedder = fasttext.load_model("morphorueval_cbow.embedding_{}.bin".format(EMBED_SIZE))
speech_part_len = len(SPEECH_PARTS)
speech_part_mapping = {str(s): num for num, s in enumerate(SPEECH_PARTS)}

cases_len = len(CASE_TAGS)
case_mapping = {str(s): num for num, s in enumerate(CASE_TAGS)}

numbers_len = len(NUMBER_TAGS)
number_mapping = {str(s): num for num, s in enumerate(NUMBER_TAGS)}

gender_len = len(GENDER_TAGS)
gender_mapping = {str(s): num for num, s in enumerate(GENDER_TAGS)}

tense_len = len(TENSE_TAGS)
tense_mapping = {str(s): num for num, s in enumerate(TENSE_TAGS)}

animacy_len = len(ANIMACY_TAGS)
animacy_mapping = {str(s): num for num, s in enumerate(ANIMACY_TAGS)}

BATCH_SIZE = 9
def _chunks(lst, n):
    result = []
    for i in range(0, len(lst), n):
        result.append(lst[i:i + n])
    return result

def batchify_dataset(train_morph, sp, case, number, gender, tense, train_morphem, target_morphem, batch_size):
    return (
        pad_sequences(_chunks(train_morph, batch_size), padding='post', dtype=np.float32, maxlen=batch_size),
        pad_sequences(_chunks(sp, batch_size), padding='post', dtype=np.int8, maxlen=batch_size),
        pad_sequences(_chunks(case, batch_size), padding='post', dtype=np.int8, maxlen=batch_size),
        pad_sequences(_chunks(number, batch_size), padding='post', dtype=np.int8, maxlen=batch_size),
        pad_sequences(_chunks(gender, batch_size), padding='post', dtype=np.int8, maxlen=batch_size),
        pad_sequences(_chunks(tense, batch_size), padding='post', dtype=np.int8, maxlen=batch_size),
        pad_sequences(_chunks(train_morphem, batch_size), padding='post', dtype=np.int8, maxlen=batch_size),
        pad_sequences(_chunks(target_morphem, batch_size), padding='post', dtype=np.int8, maxlen=batch_size),
        )

def build_speech_part_array(analyzer_results):
    output = [0 for _ in range(speech_part_len)]
    if analyzer_results:
        for result in analyzer_results.infos:
            output[speech_part_mapping[str(result.sp)]] = 1
    return output


def build_case_array(analyzer_results):
    output = [0 for _ in range(cases_len)]
    if analyzer_results:
        for result in analyzer_results.infos:
            output[case_mapping[str(result.tag.get_case())]] = 1
    return output


def build_number_array(analyzer_results):
    output = [0 for _ in range(numbers_len)]
    if analyzer_results:
        for result in analyzer_results.infos:
            output[number_mapping[str(result.tag.get_number())]] = 1
    return output


def build_gender_array(analyzer_results):
    output = [0 for _ in range(gender_len)]
    if analyzer_results:
        for result in analyzer_results.infos:
            output[gender_mapping[str(result.tag.get_gender())]] = 1
    return output


def build_tense_array(analyzer_results):
    output = [0 for _ in range(tense_len)]
    if analyzer_results:
        for result in analyzer_results.infos:
            output[tense_mapping[str(result.tag.get_tense())]] = 1
    return output


def build_animacy_array(analyzer_results):
    output = [0 for _ in range(animacy_len)]
    if analyzer_results:
        for result in analyzer_results.infos:
            animacy = str(result.tag.get_animacy())
            if animacy not in animacy_mapping:
                output[0] = 1
            else:
                output[animacy_mapping[animacy]] = 1
    return output

def get_subsentences_from_long_sentence(sentence):
    tail = sentence[-BATCH_SIZE:]
    subsentences = []
    while len(sentence) > BATCH_SIZE:
        subsentences.append(sentence[:BATCH_SIZE])
        sentence = sentence[BATCH_SIZE:]
    subsentences.append(tail)
    while len(subsentences[-1]) < BATCH_SIZE:
        subsentences[-1].append((Word([], 'X'), "_", "_", "_", "_"))
    return subsentences

def prepare_dataset(path, trim, word_trim_len):
    result = []
    sentence = []
    with open(path, 'r') as f:
        i = 0
        try:
            for line in f:
                i += 1
                line = line.strip()
                if not line:
                    if len(sentence) <= BATCH_SIZE:
                        while len(sentence) < BATCH_SIZE:
                            sentence.append((Word([], 'X', word_trim_len), "_", "_", "_", "_"))
                        result += sentence
                    else:
                        for subsent in get_subsentences_from_long_sentence(sentence):
                            result += subsent
                    sentence = []
                else:
                    splited = line.split('\t')
                    tags = splited[6].split('|')
                    speech_part = splited[5]
                    word_form = splited[1]
                    morphemic_parse = splited[2]
                    case = '_'
                    number = '_'
                    gender = '_'
                    tense = '_'
                    for tag in tags:
                        if tag.startswith('Case='):
                            case = tag
                        elif tag.startswith('Number='):
                            number = tag
                        elif tag.startswith('Gender='):
                            gender = tag
                        elif tag.startswith('Tense='):
                            tense = tag
                    word = parse_word(word_form, morphemic_parse, speech_part, word_trim_len)
                    sentence.append((word, case, number, gender, tense))
                if i % 1000 == 0:
                    print("Readed:", i)
        except Exception as ex:
            print("last i", i, "line '", line, "'")
            print("Splitted length", len(splited))
            raise ex

    return result[:int(len(result) * trim)]

def prepare_dataset_one_word(path, trim, word_trim_len):
    result = []
    sentence = []
    with open(path, 'r') as f:
        i = 0
        try:
            for line in f:
                i += 1
                line = line.strip()
                sentence = []

                splited = line.split('\t')
                speech_part = 'NOUN'
                word_form = splited[0]
                morphemic_parse = splited[1]
                case = '_'
                number = '_'
                gender = '_'
                tense = '_'
                word = parse_word(word_form, morphemic_parse, speech_part, word_trim_len)
                sentence.append((word, case, number, gender, tense))

                while len(sentence) < BATCH_SIZE:
                    sentence.append((Word([], 'X', word_trim_len), "_", "_", "_", "_"))
                result += sentence

                if i % 1000 == 0:
                    print("Readed:", i)
        except Exception as ex:
            print("last i", i, "line '", line, "'")
            print("Splitted length", len(splited))
            raise ex

    return result[int(len(result) * trim):]


def _pad_sequences(Xs, Ys, max_len):
    newXs = pad_sequences(Xs, padding='post', dtype=np.int8, maxlen=max_len, value=MASK_VALUE)
    newYs = pad_sequences(Ys, padding='post', maxlen=max_len, value=MASK_VALUE)
    return newXs, newYs

analyzer = pyxmorphy.MorphAnalyzer()

def processing(dataset, maxlen):
    train_encoded = []
    target_sp_encoded = []
    target_case_encoded = []
    target_number_encoded = []
    target_gender_encoded = []
    target_tense_encoded = []
    train_morphem = []
    target_morphem = []


    i = 0
    for features in dataset:
        i += 1
        word = features[0]
        analyzer_result = None
        word_text = word.get_word()
        maxlen = max(len(word_text), maxlen)
        if word_text:
            analyzer_result = analyzer.analyze(word_text, False, False, False)[0]
        word_vector = embedder.get_word_vector(word_text)
        speech_part_vector = build_speech_part_array(analyzer_result)
        case_part_vector = build_case_array(analyzer_result)
        number_vector = build_number_array(analyzer_result)
        gender_vector = build_gender_array(analyzer_result)
        tense_vector = build_tense_array(analyzer_result)
        if word.get_speech_part() not in speech_part_mapping:
            print("Strage speech part", word.get_speech_part())
            continue
        train_encoded.append(list(word_vector) + speech_part_vector + case_part_vector + number_vector + gender_vector + tense_vector)
        target_sp_encoded.append(to_categorical(speech_part_mapping[word.get_speech_part()], num_classes=len(SPEECH_PARTS)).tolist())
        target_case_encoded.append(to_categorical(case_mapping[features[1]], num_classes=len(case_mapping)).tolist())
        target_number_encoded.append(to_categorical(number_mapping[features[2]], num_classes=len(number_mapping)).tolist())
        target_gender_encoded.append(to_categorical(gender_mapping[features[3]], num_classes=len(gender_mapping)).tolist())
        target_tense_encoded.append(to_categorical(tense_mapping[features[4]], num_classes=len(tense_mapping)).tolist())

        features = []
        for index, letter in enumerate(word_text.lower()):
            letter_features = []
            vovelty = 0
            if letter in VOWELS:
                vovelty = 1
            voiced = 0
            letter_features.append(vovelty)
            if letter in LETTERS:
                letter_code = LETTERS[letter]
            elif letter in ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9']:
                letter_code = 35
            else:
                letter_code = 0
            letter_features += to_categorical(letter_code, num_classes=len(LETTERS) + 1 + 1).tolist()
            features.append(letter_features)
        train_morphem.append(np.array(features, dtype=np.int8))
        target_morphem.append(np.array([to_categorical(PARTS_MAPPING[label], num_classes=len(PARTS_MAPPING)) for label in word.get_simple_labels()]))
        if i % 1000 == 0:
            print("Vectorized:", i, "/", len(dataset))

    print("Finished vect")

    return train_encoded, target_sp_encoded, target_case_encoded, target_number_encoded, target_gender_encoded, target_tense_encoded, train_morphem, target_morphem

def vectorize_dataset(dataset_all, maxlen):
    dataset_parts = _chunks(dataset_all, int(len(dataset_all) / 100))
    with multiprocessing.Pool(processes=4) as pool:
        train_encoded = []
        target_sp_encoded = []
        target_case_encoded = []
        target_number_encoded = []
        target_gender_encoded = []
        target_tense_encoded = []
        train_morphem = []
        target_morphem = []
        async_results = []
        for part in dataset_parts:
            async_results.append(pool.apply_async(processing, (part, maxlen, )))

        print("Finished vectorization, returning results")
        for i, async_result in enumerate(async_results):
            result = async_result.get()
            print("Got result", i)
            train_encoded += result[0]
            target_sp_encoded  += result[1]
            target_case_encoded += result[2]
            target_number_encoded += result[3]
            target_gender_encoded += result[4]
            target_tense_encoded += result[5]
            train_morphem += result[6]
            target_morphem += result[7]

        padded_train_morphem, padded_target_morphem = _pad_sequences(train_morphem, target_morphem, maxlen)
        return np.asarray(train_encoded), np.asarray(target_sp_encoded), np.asarray(target_case_encoded), np.asarray(target_number_encoded), np.asarray(target_gender_encoded), np.asarray(target_tense_encoded), padded_train_morphem, padded_target_morphem

def scheduler(epoch, lr):
    if epoch < 8:
        return 0.001
    else:
        return 0.0001

class JoinedModel(object):
    def __init__(self, models_number, validation_split):
        self.models_number = models_number
        self.activation = "softmax"
        self.optimizer = Adam(learning_rate=0.001)
        self.models = []
        self.validation_split = validation_split

    def _build_model(self, maxlen):
        inp_morph = Input(name="input_morph", shape=(BATCH_SIZE, EMBED_SIZE + len(SPEECH_PARTS) + len(CASE_TAGS) + len(NUMBER_TAGS) + len(GENDER_TAGS) + len(TENSE_TAGS),))
        inp_morphem = Input(name="input_morphem", shape=(BATCH_SIZE, maxlen, len(LETTERS) + 1 + 1 + 1))
        inputs = [inp_morph, inp_morphem]

        #inp = BatchNormalization()(inp_morph)
        inp = inp_morph
        conv_outputs = []
        i = 1
        for drop, units, window_size in zip([0.4, 0.3, 0.2], [512, 256, 192], [3, 3, 3]):
            conv = Conv1D(units, window_size, padding="same", name="morphologic_convolution_" + str(i))(inp)
            pooling = MaxPooling1D(pool_size=3, data_format='channels_first')(conv)
            #norm = BatchNormalization()(pooling)
            activation = Activation('relu', name="morphologic_activation_" + str(i))(pooling)
            do = Dropout(drop, name="morphologic_dropout_" + str(i))(activation)
            inp = do
            conv_outputs.append(do)
            i += 1

        sp_output = TimeDistributed(Dense(len(SPEECH_PARTS), activation=self.activation), name="speech_part")(conv_outputs[-1])
        case_output = TimeDistributed(Dense(len(CASE_TAGS), activation=self.activation), name="case")(conv_outputs[-1])
        number_output = TimeDistributed(Dense(len(NUMBER_TAGS), activation=self.activation), name="number")(conv_outputs[-1])
        gender_output = TimeDistributed(Dense(len(GENDER_TAGS), activation=self.activation), name="gender")(conv_outputs[-1])
        tense_output = TimeDistributed(Dense(len(TENSE_TAGS), activation=self.activation), name="tense")(conv_outputs[-1])
        outputs = [sp_output, case_output, number_output, gender_output, tense_output,]

        repeated_sp = TimeDistributed(RepeatVector(maxlen), name="repeated_sp")(sp_output)
        #repeated_case = TimeDistributed(RepeatVector(maxlen), name="repeated_case")(case_output)
        #repeated_number = TimeDistributed(RepeatVector(maxlen), name="repeated_number")(number_output)
        #repeated_gender = TimeDistributed(RepeatVector(maxlen), name="repeated_gender")(gender_output)
        #repeated_tense = TimeDistributed(RepeatVector(maxlen), name="repeated_tense")(tense_output)
        #repeated_conv_outputs = TimeDistributed(RepeatVector(maxlen), name="repeated_conv")(conv_outputs[-1])
        #print("Conv outputs shape", conv_outputs[-1].shape)

        morphem_features = inp_morphem
        #concat = Concatenate(name="concattags")([morphem_features, repeated_sp, repeated_case, repeated_number, repeated_gender, repeated_tense])
        concat = Concatenate(name="concattags")([morphem_features, repeated_sp])
        #concat = Concatenate(name="concattags")([morphem_features, repeated_conv_outputs])

        #morphem_model_input = Input(shape=(maxlen, len(LETTERS) + 1 + 1 + 1 + 1 + speech_part_len + cases_len + numbers_len + gender_len + tense_len))
        morphem_model_input = Input(shape=(maxlen, len(LETTERS) + 1 + 1 + 1 + speech_part_len))
        #morphem_model_input = Input(shape=(maxlen, len(LETTERS) + 1 + 1 + 1 + 85))

        morphem_convolutions = [morphem_model_input]
        i = 1
        for drop, units, window_size in zip([0.4, 0.4, 0.4], [512, 256, 192], [5, 5, 5]):
            conv = Conv1D(units, window_size, padding="same", name="morphemic_convolution_" + str(i))(morphem_convolutions[-1])
            pooling = MaxPooling1D(pool_size=3, data_format='channels_first', name="morphemic_pooling_" + str(i))(conv)
            #norm = BatchNormalization()(pooling)
            activation = Activation('relu', name="morphemic_activation_" + str(i))(pooling)
            do = Dropout(drop, name="morphemic_dropout_" + str(i))(activation)
            morphem_convolutions.append(do)
            i += 1

        print("Morphem convolutions shape", morphem_convolutions[-1].shape)
        morphem_outputs = [TimeDistributed(
                Dense(len(PARTS_MAPPING), activation=self.activation), name="morphemic_dense")(morphem_convolutions[-1])]

        self.morphem_model = Model(inputs=[morphem_model_input], outputs=morphem_outputs, name="submodel_morphemic")
        self.morphem_model = keras.models.load_model("keras_morphem_for_joined_1628259998_20.h5")
        self.morphem_model.trainable = False
        outputs.append(TimeDistributed(self.morphem_model, name="morphem_distributed")(concat))
        print("Total outputs", len(outputs))
        print("Append model")
        self.models.append(Model(inputs, outputs=outputs))

        self.models[-1].compile(loss='categorical_crossentropy',
                                optimizer=self.optimizer, metrics=['acc'])


        print(self.models[-1].summary())

    def load(self, path):
        self.maxlen = 20
        self.models.append(keras.models.load_model(path))

    def train(self, words, epochs_train, epochs_tune):
        Xs, Y_sp, Y_case, Y_number, Y_gender, Y_tense, train_morphem, target_morphem = vectorize_dataset(words, 20)
        bXs, bY_sp, bY_case, bY_number, bY_gender, bY_tense, btrain_morphem, btarget_morphem = batchify_dataset(Xs, Y_sp, Y_case, Y_number, Y_gender, Y_tense, train_morphem, target_morphem, BATCH_SIZE)
        for i in range(self.models_number):
            self.maxlen = 20
            self._build_model(20)
        print("Total models", len(self.models))
        morpheme_targets = btarget_morphem
        print("Traing morphem shape", btrain_morphem.shape)
        #print("Target morphem shape", btarget_morphem.shape)
        #print("Target morphem 0", btarget_morphem[0])
        #print("Target morphem 00", btarget_morphem[0][0])
        ##morpheme_targets = [btarget_morphem]
        print("Total target morpheme", len(morpheme_targets))
        print("Targets zero shape", morpheme_targets[0].shape)
        print("Targets zero zero zero shape", morpheme_targets[0][0].shape)
        print("Targets zero zero", morpheme_targets[0][0][0:20])
        #es1 = EarlyStopping(monitor='val_speech_part_acc', patience=10, verbose=1)
        #es2 = EarlyStopping(monitor='val_case_acc', patience=10, verbose=1)
        for i, model in enumerate(self.models):
            print("Training", i)
            model.fit([bXs, btrain_morphem], [bY_sp, bY_case, bY_number, bY_gender, bY_tense, morpheme_targets], epochs=epochs_train, verbose=2,
                      callbacks=[], validation_split=self.validation_split, batch_size=2048)
            print("Path", "keras_model_joined_em_{}_{}_normal.h5".format(EMBED_SIZE, int(time.time())))
            model.save("keras_model_joined_em_{}_{}_normal.h5".format(EMBED_SIZE, int(time.time())))
            print("Train finished", i)


        self.morphem_model.trainable = True
        self.models[-1].compile(loss='categorical_crossentropy',
                                optimizer=Adam(learning_rate=1e-5), metrics=['acc'])
        print("Fine tuning")
        for i, model in enumerate(self.models):
            model.fit([bXs, btrain_morphem], [bY_sp, bY_case, bY_number, bY_gender, bY_tense, morpheme_targets], epochs=epochs_tune, verbose=2,
                      callbacks=[], validation_split=self.validation_split, batch_size=2048)
            print("Path", "keras_model_joined_em_{}_{}_fine_tuned.h5".format(EMBED_SIZE, int(time.time())))
            model.save("keras_model_joined_em_{}_{}_fine_tuned.h5".format(EMBED_SIZE, int(time.time())))
            print("Train finished", i)

        #quantize_model = tfmot.quantization.keras.quantize_model
        #self.q_aware_model = quantize_model(self.models[-1])

        #self.q_aware_model.compile(loss='categorical_crossentropy',
        #                        optimizer=Adam(learning_rate=1e-5), metrics=['acc'])

        #self.q_aware_model.fit([bXs, btrain_morphem], [bY_sp, bY_case, bY_number, bY_gender, bY_tense, morpheme_targets], epochs=5, verbose=2,
        #              callbacks=[], validation_split=self.validation_split, batch_size=2048)

        return bXs, btrain_morphem
        #print("Pruning")
        #prune_low_magnitude = tfmot.sparsity.keras.prune_low_magnitude

        #batch_size = 2048
        #epochs = 2
        #validation_split = 0.1 # 10% of training set will be used for validation set.

        #num_images = len(Xs) * (1 - validation_split)
        #end_step = np.ceil(num_images / batch_size).astype(np.int32) * epochs

        ## Define model for pruning.
        #pruning_params = {
        #      'pruning_schedule': tfmot.sparsity.keras.PolynomialDecay(initial_sparsity=0.50,
        #                                                               final_sparsity=0.80,
        #                                                               begin_step=0,
        #                                                               end_step=end_step)

        #}
        #callbacks = [
        #    tfmot.sparsity.keras.UpdatePruningStep(),
        #    tfmot.sparsity.keras.PruningSummaries(log_dir="."),
        #]

        #model_for_pruning = prune_low_magnitude(self.models[-1], **pruning_params)
        #model_for_pruning.compile(loss='categorical_crossentropy',
        #                        optimizer=Adam(learning_rate=1e-5), metrics=['acc'])
        #model_for_pruning.summary()

        #model_for_pruning.fit([bXs, btrain_morphem], [bY_sp, bY_case, bY_number, bY_gender, bY_tense, morpheme_targets], epochs=2, verbose=2,
        #              callbacks=callbacks, validation_split=self.validation_split, batch_size=2048)


    def classify(self, words, q_aware=False):
        print("Total models:", len(self.models))
        Xs, Y_SP, Y_CASE, Y_NUMBER, Y_GENDER, Y_TENSE, train_morphem, target_morphem = [np.asarray(elem) for elem in vectorize_dataset(words, self.maxlen)]
        bXs, bY_sp, bY_case, bY_number, bY_gender, bY_tense, btrain_morphem, btarget_morphem = batchify_dataset(Xs, Y_SP, Y_CASE, Y_NUMBER, Y_GENDER, Y_TENSE, train_morphem, target_morphem, BATCH_SIZE)
        print("Word zero", words[0][0].get_word())
        print("Parse zero", words[0][0])
        print("Train for word zero", btrain_morphem[0][0])
        print("Classes for word zero", btarget_morphem[0][0])

        print("Word 9", words[10][0].get_word())
        print("Parse 9", words[10][0])
        print("Train for word 9", btrain_morphem[1][1])
        print("Classes for word 9", btarget_morphem[1][1])

        if q_aware:
            predictions = self.q_aware_model.predict([bXs, btrain_morphem])
        else:
            predictions = self.models[0].predict([bXs, btrain_morphem])

        pred_sp, pred_case, pred_number, pred_gender, pred_tense = predictions[0:5]
        pred_class_sp = pred_sp.argmax(axis=-1)
        pred_class_case = pred_case.argmax(axis=-1)
        pred_class_number = pred_number.argmax(axis=-1)
        pred_class_gender = pred_gender.argmax(axis=-1)
        pred_class_tense = pred_tense.argmax(axis=-1)
        #pred_class_animacy = pred_animacy.argmax(axis=-1)
        Ysps = bY_sp.argmax(axis=-1)
        Ycases = bY_case.argmax(axis=-1)
        Ynumbers = bY_number.argmax(axis=-1)
        Ygenders = bY_gender.argmax(axis=-1)
        Ytences = bY_tense.argmax(axis=-1)
        total_error = set([])
        total_words = sum(1 for word in words if word[0].get_word())

        print("Total morph words", len(Ysps) * BATCH_SIZE)
        print("Total real morph words", total_words)
        print("Total real morph words part", total_words / (len(Ysps) * BATCH_SIZE))

        error_sps = 0
        word_index = 0
        errors = {}
        for pred_sent, real_sent in zip(pred_class_sp, Ysps):
            for pred_word, real_word in zip(pred_sent, real_sent):
                if word_index < len(words) and words[word_index][0].get_word():
                    if pred_word != real_word:
                        expected_sp = str(SPEECH_PARTS[real_word])
                        got_sp = str(SPEECH_PARTS[pred_word])
                        if expected_sp not in errors:
                            errors[expected_sp] = {}
                        if got_sp not in errors[expected_sp]:
                            errors[expected_sp][got_sp] = 0
                        errors[expected_sp][got_sp] += 1

                        total_error.add(word_index)
                        error_sps += 1
                word_index += 1

        old_errors = len(total_error)
        print(errors)
        print("Errors added by SP:", old_errors)
        print("Total words:", total_words)
        print("Error words:", error_sps)
        print("Error rate SPEECH PART:", float(error_sps) / total_words)
        print("Correct rate SPEECH_PART:", float(total_words - error_sps) / total_words)

        error_cases = 0
        word_index = 0

        case_errors = {}
        for pred_sent, real_sent in zip(pred_class_case, Ycases):
            for pred_word, real_word in zip(pred_sent, real_sent):
                if word_index < len(words) and words[word_index][0].get_word():

                    if pred_word != real_word:
                        expected_case = str(CASE_TAGS[real_word])
                        got_case = str(CASE_TAGS[pred_word])
                        if expected_case not in case_errors:
                            case_errors[expected_case] = {}
                        if got_case not in case_errors[expected_case]:
                            case_errors[expected_case][got_case] = 0
                        case_errors[expected_case][got_case] += 1

                        total_error.add(word_index)
                        error_cases += 1
                word_index += 1

        print("CaseErrors", case_errors)
        print("Erros added by case:", len(total_error) - old_errors)
        old_errors = len(total_error)
        print("Total words:", total_words)
        print("Error words:", error_cases)
        print("Error rate Case:", float(error_cases) / total_words)
        print("Correct rate Case:", float(total_words - error_cases) / total_words)

        error_numbers = 0
        word_index = 0
        for pred_sent, real_sent in zip(pred_class_number, Ynumbers):
            for pred_word, real_word in zip(pred_sent, real_sent):
                if word_index < len(words) and words[word_index][0].get_word():
                    if pred_word != real_word:
                        total_error.add(word_index)
                        error_numbers += 1
                word_index += 1

        print("Erros added by number:", len(total_error) - old_errors)
        old_errors = len(total_error)

        print("Total words:", total_words)
        print("Error words:", error_numbers)
        print("Error rate numbers:", float(error_numbers) / total_words)
        print("Correct rate numbers:", float(total_words - error_numbers) / total_words)

        error_genders = 0
        word_index = 0
        for pred_sent, real_sent in zip(pred_class_gender, Ygenders):
            for pred_word, real_word in zip(pred_sent, real_sent):
                if word_index < len(words) and words[word_index][0].get_word():
                    if pred_word != real_word:
                        total_error.add(word_index)
                        error_genders += 1
                word_index += 1

        print("Erros added by gender:", len(total_error) - old_errors)
        old_errors = len(total_error)

        print("Total words:", total_words)
        print("Error words:", error_genders)
        print("Error rate genders:", float(error_genders) / total_words)
        print("Correct rate genders:", float(total_words - error_genders) / total_words)

        error_tences = 0
        word_index = 0
        for pred_sent, real_sent in zip(pred_class_tense, Ytences):
            for pred_word, real_word in zip(pred_sent, real_sent):
                if word_index < len(words) and words[word_index][0].get_word():
                    if pred_word != real_word:
                        total_error.add(word_index)
                        error_tences += 1
                word_index += 1

        print("Erros added by tense:", len(total_error) - old_errors)
        old_errors = len(total_error)

        print("Total words:", total_words)
        print("Error words:", error_tences)
        print("Error rate tences:", float(error_tences) / total_words)
        print("Correct rate tences:", float(total_words - error_tences) / total_words)

        print("Total error words:", len(total_error))
        print("Total correctness:", float(total_words - len(total_error)) / total_words)

        reverse_mapping = {v: k for k, v in PARTS_MAPPING.items()}

        def classify_morphem_handmande():
            morphem_predictions = predictions[5:]
            #print("Morphem predictions shape", morphem_predictions[0].shape)
            #print("Morphem predictions", morphem_predictions[0][0:10])
            morphem_classes = [pred.argmax(axis=-1) for pred in morphem_predictions]
            #print("Morphem classes", morphem_classes[0][0:10])
            result = []
            for i, batch in enumerate(words):
                word = batch[0]
                word_text = word.get_word()
                raw_parse = []
                for j, letter in enumerate(word_text):
                    predicted_class = morphem_classes[j][i]
                    raw_parse.append(reverse_mapping[int(predicted_class)])
                parse = _transform_classification(raw_parse)
                result.append(parse)
            print(measure_quality(result, [w[0].get_labels() for w in words], [w[0].get_word() for w in words], True))

        def classify_morphem():
            pred_class = predictions[5].argmax(axis=-1)
            result = []
            for i, batch in enumerate(words):
                word = batch[0]
                word_text = word.get_word()
                cutted_prediction = pred_class[i][:len(word_text)]
                raw_parse = [reverse_mapping[int(num)] for num in cutted_prediction]
                parse = _transform_classification(raw_parse)
                result.append(parse)
            print(measure_quality(result, [w[0].get_labels() for w in words], [w[0].get_word() for w in words], False))

        def classify_batch():
            morphem_predictions = predictions[5]
            #print("Predictions", morphem_predictions[0][0])
            morphem_classes = [pred.argmax(axis=-1) for pred in morphem_predictions]
            #print("Morphem classes length", len(morphem_classes))
            #print("First morphem classes shape", morphem_classes[0].shape)
            #print("First morphem classes value", morphem_classes[0][0:20])
            morphem_classes_arr = np.asarray(morphem_classes)
            morphem_classes = morphem_classes_arr.reshape(len(morphem_classes) * morphem_classes[0].shape[0], morphem_classes[0].shape[1])
            #print("Morphem classes", morphem_classes.shape)
            #print("Morphem classes value", morphem_classes[0])
            result = []
            def is_real_word(word):
                return len(word.get_word()) > 2 and not all(label.endswith('UNKN') for label in word.get_labels())

            for i, batch in enumerate(words):
                word = batch[0]
                word_text = word.get_word()
                if not is_real_word(word):
                    continue
                cutted_prediction = morphem_classes[i][:len(word_text)]
                raw_parse = [reverse_mapping[int(num)] for num in cutted_prediction]
                parse = _transform_classification(raw_parse)
                result.append(parse)

            print("Total words", len(words))
            print("Total real morphem words", sum(1 for w in words if is_real_word(w[0])))
            print("Total real morphem words part", sum(1 for w in words if is_real_word(w[0])) / len(words))
            print(measure_quality(result, [w[0].get_labels() for w in words if is_real_word(w[0])], [w[0].get_word() for w in words if is_real_word(w[0])], True))

        classify_batch()


if __name__ == "__main__":
    WORD_TRIM_LEN = 20
    train_txt = prepare_dataset("./datasets/labeled_sytagrus_better_group.train", 1, WORD_TRIM_LEN)
    test_txt = prepare_dataset("./datasets/labeled_sytagrus_better_group.test", 1, WORD_TRIM_LEN)
    #test_single_word_txt = prepare_dataset_one_word("datasets/lexemes_with_short_adjectives_lexeme_group.test", 0.5, WORD_TRIM_LEN)

    model = JoinedModel(1, 0.1)
    #model.load("keras_model_joined_em_50_1632145592_fine_tuned.h5")

    bXs, btrain_morphem = model.train(train_txt, 80, 40)

    converter = tflite.TFLiteConverter.from_keras_model(model.models[-1])
    tflite_model = converter.convert()
    with open('joined_tflite_model{}_new_9_20.tflite'.format(str(int(time.time()))), 'wb') as f:
        f.write(tflite_model)

    #converter = tflite.TFLiteConverter.from_keras_model(model.q_aware_model)
    #tflite_model = converter.convert()
    #with open('joined_tflite_model{}_new_9_20_q_aware.tflite'.format(str(int(time.time()))), 'wb') as f:
    #    f.write(tflite_model)

#    def representative_dataset():
#        for xs, train_morphem in zip(bXs[0:100], btrain_morphem[0:100]):
#            print("xs shape", xs.shape)
#            print("morphem shape", train_morphem.shape)
#            yield [np.asarray([xs], dtype=np.float32), np.asarray([train_morphem], dtype=np.float32)]

    #converter.optimizations = [tflite.Optimize.DEFAULT]
    #converter.representative_dataset = representative_dataset

    #tflite_int8_model = converter.convert()
    #with open('joined_tflite_model{}_new_9_20_int8_full.tflite'.format(str(int(time.time()))), 'wb') as f:
    #    f.write(tflite_int8_model)

    #converter.target_spec.supported_types = [tf.float16]

    #tflite_fp16_model = converter.convert()
    #with open('joined_tflite_model{}_new_9_20_fp16.tflite'.format(str(int(time.time()))), 'wb') as f:
    #    f.write(tflite_fp16_model)

    #model.classify(test_single_word_txt, q_aware=False)
    model.classify(test_txt, q_aware=False)
    #model.classify(test_txt, q_aware=True)
