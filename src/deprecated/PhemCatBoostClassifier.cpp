#include <ml/PhemCatBoostClassifier.h>
#include <utils/UniCharacter.h>
#include <utils/UniString.h>
#include <unordered_map>
namespace ml {
    static std::unordered_map<utils::UniCharacter, size_t> CIPH = {
        {utils::UniCharacter::O, 109830},
        {utils::UniCharacter::YE, 84830},
        {utils::UniCharacter::A, 79980},
        {utils::UniCharacter::I, 73670},
        {utils::UniCharacter::N, 67000},
        {utils::UniCharacter::T, 63180},
        {utils::UniCharacter::S, 54730},
        {utils::UniCharacter::R, 47460},
        {utils::UniCharacter::V, 45330},
        {utils::UniCharacter::L, 43430},
        {utils::UniCharacter::K, 34860},
        {utils::UniCharacter::M, 32030},
        {utils::UniCharacter::D, 29770},
        {utils::UniCharacter::P, 28040},
        {utils::UniCharacter::UY, 26150},
        {utils::UniCharacter::YA, 20010},
        {utils::UniCharacter::AUY, 18980},
        {utils::UniCharacter::MG, 17350},
        {utils::UniCharacter::G, 16870},
        {utils::UniCharacter::ZE, 16410},
        {utils::UniCharacter::B, 15920},
        {utils::UniCharacter::CH, 14500},
        {utils::UniCharacter::YI, 12080},
        {utils::UniCharacter::H, 9660},
        {utils::UniCharacter::JE, 9400},
        {utils::UniCharacter::SH, 7180},
        {utils::UniCharacter::UY, 6390},
        {utils::UniCharacter::CE, 4860},
        {utils::UniCharacter::SHE, 3610},
        {utils::UniCharacter::AE, 3310},
        {utils::UniCharacter::F, 2670},
        {utils::UniCharacter::TV, 370},
        {utils::UniCharacter::YO, 130}};

    //(0, "н"), # letter itself (cat 0)
    //(1, "CONSONANT"), # vowel or consonant (cat 1)
    //(2, 0), # index in word (num 0)
    //(3, 67000), # CIPH law (num 1)
    //(4, 5) # Harris law forward (num 2)
    //(5, 2) # Harris law backward (num 3)
    //(6, '1') # Can be prefix (cat 2)
    //(7, ""), # word[index-3] (cat 3)
    //(8, ""), # word[index-2] (cat 4)
    //(9, ""), # word[index-1] (cat 5)
    //(10, "а"),# word[index+1] (cat 6)
    //(11, "в"),# word[index+2] (cat 7)
    //(12, "л"),# word[index+3] (cat 8)
    //(13, "PRTF"),  # speech part (cat 8)
    //(14, "nomn"), # case         (cat 10)
    //(15, "masc"), # gender       (cat 11)
    //(16, "sing"), # number       (cat 12)
    //(17, "past"), # tense        (cat 13)
    //(18, 11),     # word length  (num 4)
    //(19, 11)      # stem length  (num 5)
    void printFeatures(const PhemCatBoostClassifier::NumFeatures& numFeatures, const PhemCatBoostClassifier::CatFeatures& catFeatures)
    {
        std::cerr << "NumFeatures:" << numFeatures.size() << " CatFeatures:" << catFeatures.size() << " ";
        std::cerr <<  catFeatures[0] << " ";
        std::cerr << catFeatures[1] << " ";
        std::cerr << numFeatures[0] << " ";
        std::cerr << numFeatures[1] << " ";
        std::cerr << numFeatures[2] << " ";
        std::cerr << numFeatures[3] << " ";
        std::cerr << catFeatures[2] << " ";
        std::cerr << catFeatures[3] << " ";
        std::cerr << catFeatures[4] << " ";
        std::cerr << catFeatures[5] << " ";
        std::cerr << catFeatures[6] << " ";
        std::cerr << catFeatures[7] << " ";
        std::cerr << catFeatures[8] << " ";
        std::cerr << catFeatures[9] << " ";
        std::cerr << catFeatures[10] << " ";
        std::cerr << catFeatures[11] << " ";
        std::cerr << catFeatures[12] << " ";
        std::cerr << catFeatures[13] << " ";
        std::cerr << numFeatures[4] << " ";
        std::cerr << numFeatures[5] << " ";
        std::cerr <<  std::endl;

    }

    std::pair<PhemCatBoostClassifier::NumFeatures, PhemCatBoostClassifier::CatFeatures> PhemCatBoostClassifier::getPhemFreautres(analyze::WordFormPtr wf, const utils::UniString& upperCaseWf, std::size_t letterIndex) const {
        NumFeatures numResult = {{0.0}};
        CatFeatures catResult = {{""}};
        size_t size = upperCaseWf.length();
        analyze::MorphInfo mi = *(wf->getMorphInfo().begin());
        utils::UniCharacter letter = upperCaseWf[letterIndex];
        utils::UniString wordPrefix = upperCaseWf.subString(0, letterIndex);
        catResult[0] = letter.getInnerRepr();
        catResult[1] = utils::UniCharacter::VOWELS.count(letter) ? "VOWEL" : "CONSONANT";
        catResult[2] = prefDict.count(wordPrefix) ? "1" : "0";
        catResult[3] = letterIndex < 3 ? "" : upperCaseWf[letterIndex - 3].getInnerRepr();
        catResult[4] = letterIndex < 2 ? "" : upperCaseWf[letterIndex - 2].getInnerRepr();
        catResult[5] = letterIndex < 1 ? "" : upperCaseWf[letterIndex - 1].getInnerRepr();
        catResult[6] = letterIndex + 1 >= size ? "" : upperCaseWf[letterIndex + 1].getInnerRepr();
        catResult[7] = letterIndex + 2 >= size ? "" : upperCaseWf[letterIndex + 2].getInnerRepr();
        catResult[8] = letterIndex + 3 >= size ? "" : upperCaseWf[letterIndex + 3].getInnerRepr();
        catResult[9] = to_string(mi.sp);
        catResult[10] = mi.tag.getCase() == base::MorphTag::UNKN ? "" : to_string(mi.tag.getCase());
        catResult[11] = mi.tag.getGender() == base::MorphTag::UNKN ? "" : to_string(mi.tag.getGender());
        catResult[12] = mi.tag.getNumber() == base::MorphTag::UNKN ? "" : to_string(mi.tag.getNumber());
        catResult[13] = mi.tag.getTense() == base::MorphTag::UNKN ? "" : to_string(mi.tag.getTense());

        numResult[0] = letterIndex;
        numResult[1] = CIPH[letter];
        numResult[2] = letterIndex == 0 ? 0 : dict->countPrefix(wordPrefix);
        numResult[3] = letterIndex == upperCaseWf.length() - 1 ? 0 : dict->countSuffix(upperCaseWf.rcut(upperCaseWf.length() - letterIndex));
        numResult[4] = size;
        numResult[5] = mi.stemLen;

        return std::make_pair(numResult, catResult);
    }

    std::pair<std::vector<PhemCatBoostClassifier::NumFeatures>, std::vector<PhemCatBoostClassifier::CatFeatures>> PhemCatBoostClassifier::getPhemFreautres(analyze::WordFormPtr wf) const {
        utils::UniString upCaseWf = wf->getWordForm().toUpperCase();
        std::vector<NumFeatures> numResult;
        std::vector<CatFeatures> catResult;
        for (size_t i = 0; i < upCaseWf.length(); ++i) {
            auto [num, cat] = getPhemFreautres(wf, upCaseWf, i);
            numResult.push_back(num);
            catResult.push_back(cat);
        }
        return std::make_pair(numResult, catResult);
    }

    void PhemCatBoostClassifier::classify(analyze::WordFormPtr wf) const {
        auto features = getPhemFreautres(wf);

        auto classes = predictSequence(features.first, features.second);
        std::vector<base::PhemTag> result;
        for (size_t cls : classes) {
            switch (cls) {
                case 0:
                    result.push_back(base::PhemTag::PREFIX);
                    break;
                case 1:
                    result.push_back(base::PhemTag::ROOT);
                    break;
                case 2:
                    result.push_back(base::PhemTag::SUFFIX);
                    break;
                case 3:
                    result.push_back(base::PhemTag::ENDING);
                    break;
                default:
                    result.push_back(base::PhemTag::UNKN);
                    break;
            }
        }
        wf->setPhemInfo(result);
    }
} // namespace ml
