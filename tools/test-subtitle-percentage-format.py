#!/usr/bin/env python3
"""Run actual slider GetText/FormatText and StringUtils::Format with fmt headers.

Only settings/localizer/variant/log boundary objects are adapters. This is host
formatting verification, not GUI rendering, target compilation or device playback.
"""
import argparse
import ast
from collections import Counter
import copy
import json
import os
from pathlib import Path
import runpy
import signal
import subprocess
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
FMT = Path(os.environ.get('CE_FMT_INCLUDE', '/usr/include'))
BASELINE = '48f6571ccc'
FUNCTION = runpy.run_path(str(ROOT / 'tools/test-render-slot-publication.py'))['function']
IDS = ('subtitles.bitmapoffset', 'subtitles.bitmapmargin')


def po(text):
    entries, entry, field = {}, {}, None
    for raw in text.splitlines() + ['']:
        line = raw.strip()
        if not line:
            if 'msgctxt' in entry:
                assert entry['msgctxt'] not in entries, 'duplicate PO ID: ' + entry['msgctxt']
                entries[entry['msgctxt']] = entry
            entry, field = {}, None
        elif line.startswith(('msgctxt ', 'msgid ', 'msgstr ')):
            field, value = line.split(' ', 1)
            entry[field] = ast.literal_eval(value)
        elif line.startswith('"') and field:
            entry[field] += ast.literal_eval(line)
    return entries


def canonical(node):
    return node.tag, sorted(node.attrib.items()), (node.text or '').strip(), [canonical(c) for c in node]


def configuration():
    xml = ET.parse(ROOT / 'system/settings/settings.xml').getroot()
    settings = xml.findall('.//setting')
    assert all(n == 1 for n in Counter(s.get('id') for s in settings).values()), 'duplicate setting ID'
    current = {s.get('id'): s for s in settings}
    old_xml = subprocess.check_output(['git', 'show', BASELINE + ':system/settings/settings.xml'], cwd=ROOT, text=True)
    old = {s.get('id'): s for s in ET.fromstring(old_xml).findall('.//setting')}
    labels = {}
    for name in IDS:
        node = current[name]
        assert node.get('type') == 'number'
        assert node.find('control').attrib == {'type': 'slider', 'format': 'number'}
        assert node.findtext('control/formatlabel') == '69365', name
        masked = copy.deepcopy(node)
        masked.find('control/formatlabel').text = old[name].findtext('control/formatlabel')
        assert canonical(masked) == canonical(old[name]), 'changed default/range/dependency: ' + name
        labels[name] = int(node.findtext('control/formatlabel'))
    assert [s.get('id') for s in settings if s.findtext('control/formatlabel') == '69365'] == list(IDS)
    languages = {}
    for lang in ('en_gb', 'de_de'):
        path = f'addons/resource.language.{lang}/resources/strings.po'
        entries = po((ROOT / path).read_text())
        assert entries['#69365']['msgid'] == '{0:.1f} %'
        old_entries = po(subprocess.check_output(['git', 'show', BASELINE + ':' + path], cwd=ROOT, text=True))
        assert entries['#14047'] == old_entries['#14047'], 'shared integer translation changed'
        assert entries['#14047']['msgid'] == '{0:d} %'
        languages[lang] = {n: entries['#' + str(n)]['msgstr'] or entries['#' + str(n)]['msgid'] for n in (14047, 69365)}
    assert languages['de_de'][69365] == '{0:.1f} %'
    english = po((ROOT / 'addons/resource.language.en_gb/resources/strings.po').read_text())
    german = po((ROOT / 'addons/resource.language.de_de/resources/strings.po').read_text())
    assert english['#69336']['msgid'] == german['#69336']['msgid']
    assert german['#69336']['msgstr']
    # Trace the actual number Update branch, and the separate in-playback controls.
    gui = (ROOT / 'xbmc/settings/windows/GUIControlSettings.cpp').read_text()
    update = FUNCTION(gui, 'void CGUIControlSliderSetting::Update(')
    number = update[update.index('case SettingType::Number:'):]
    assert 'double value;' in number and 'settingNumber->GetMinimum()' in number
    assert 'CGUIControlSliderSetting::GetText(m_pSetting, value,' in number
    dialog = (ROOT / 'xbmc/video/dialogs/GUIDialogSubtitleSettings.cpp').read_text()
    for start, end in [('  auto offset = AddSlider(', '  offset->SetDependencies'), ('  auto margin = AddSlider(', '  margin->SetHelp')]:
        control = dialog[dialog.index(start):dialog.index(end)]
        assert '69365,' in control and '14047,' not in control, 'in-playback label still integer'
    return labels, languages


PREFIX = r'''
#include <cassert>
#include <functional>
#include <iostream>
#include <map>
#include <memory>
#include <string>
#include <variant>
#include <vector>
#include "utils/StringUtils.h"

struct CVariant {
 std::variant<int,double,std::string> value;
 CVariant(int v):value(v){} CVariant(double v):value(v){} CVariant(const char*v):value(std::string(v)){}
 bool isDouble()const{return std::holds_alternative<double>(value);}
 bool isInteger()const{return std::holds_alternative<int>(value);}
 double asDouble()const{return std::get<double>(value);} long asInteger()const{return std::get<int>(value);}
};
struct CSettingControlSlider;
using SettingControlSliderFormatter=std::string(*)(const std::shared_ptr<const CSettingControlSlider>&,const CVariant&,const CVariant&,const CVariant&,const CVariant&);
struct CSettingControlSlider {
 int label=-1;std::string m_format="number",format="{:.1f}";
 SettingControlSliderFormatter GetFormatter()const{return nullptr;}
 const std::string& GetFormatString()const{return format;} int GetFormatLabel()const{return label;}
 std::string GetDefaultFormatString()const;
};
struct CSetting {
 std::string id;std::shared_ptr<CSettingControlSlider> control=std::make_shared<CSettingControlSlider>();
 std::shared_ptr<CSettingControlSlider> GetControl()const{return control;} const std::string& GetId()const{return id;}
};
struct ILocalizer {std::map<int,std::string> strings;};
std::string Localize(int id,ILocalizer* localizer){return localizer->strings.at(id);}
constexpr int LOGERROR=4;
struct CLog {
 static inline std::vector<std::string> errors;
 template<class... Args> static void Log(int level,const std::string& format,Args&&... args){
  assert(level==LOGERROR);errors.push_back(StringUtils::Format(format,std::forward<Args>(args)...));}
};
struct CGUIControlSliderSetting {
 static std::string GetText(const std::shared_ptr<CSetting>&,const CVariant&,const CVariant&,const CVariant&,const CVariant&,ILocalizer*);
 static bool FormatText(const std::string&,const CVariant&,const std::string&,std::string&);
};
'''


def code(labels, languages):
    gui = (ROOT / 'xbmc/settings/windows/GUIControlSettings.cpp').read_text()
    control = (ROOT / 'xbmc/settings/SettingControl.cpp').read_text()
    body = PREFIX + '\n' + FUNCTION(control, 'std::string CSettingControlSlider::GetDefaultFormatString() const')
    body += '\n' + FUNCTION(gui, 'std::string CGUIControlSliderSetting::GetText(')
    body += '\n' + FUNCTION(gui, 'bool CGUIControlSliderSetting::FormatText(')
    body += '\nint main(){\n'
    cases = {
        IDS[0]: [(-100.0, '-100.0 %'), (-0.1, '-0.1 %'), (0.0, '0.0 %'), (0.1, '0.1 %'), (12.3, '12.3 %'), (100.0, '100.0 %')],
        IDS[1]: [(0.0, '0.0 %'), (0.1, '0.1 %'), (1.0, '1.0 %'), (2.3, '2.3 %'), (10.0, '10.0 %')],
    }
    for lang, strings in languages.items():
        body += '{ ILocalizer loc;\n'
        for label, text in strings.items():
            body += f'loc.strings[{label}]={json.dumps(text, ensure_ascii=False)};\n'
        for name, samples in cases.items():
            minimum, step, maximum = (-100.0, 0.1, 100.0) if name == IDS[0] else (0.0, 0.1, 10.0)
            body += 'auto ' + ('offset' if name == IDS[0] else 'margin') + '=std::make_shared<CSetting>();\n'
            var = 'offset' if name == IDS[0] else 'margin'
            body += f'{var}->id={json.dumps(name)};{var}->control->label={labels[name]};\n'
            for value, expected in samples:
                body += f'assert(CGUIControlSliderSetting::GetText({var},CVariant({value}),{minimum},{step},{maximum},&loc)=={json.dumps(expected)});\n'
            body += 'assert(CLog::errors.empty());\n'
            # Reproduce the reported old double formatting exception and actual fallback.
            body += f'{var}->control->label=14047;\n'
            body += f'assert(CGUIControlSliderSetting::GetText({var},CVariant(1.0),{minimum},{step},{maximum},&loc)=="1.0");\n'
            body += 'assert(CLog::errors.size()==1);assert(CLog::errors.back().find("invalid format specifier")!=std::string::npos);\n'
            body += f'assert(CLog::errors.back().find({var}->id)!=std::string::npos);CLog::errors.clear();\n'
        # Preserve true integer formatting, including German nonbreaking spacing.
        body += 'auto integer=std::make_shared<CSetting>();integer->id="integer.percentage";integer->control->label=14047;integer->control->m_format="integer";\n'
        separator = '\u00a0' if lang == 'de_de' else ' '
        for value in (-100, 0, 1, 100):
            body += f'assert(CGUIControlSliderSetting::GetText(integer,CVariant({value}),0,1,100,&loc)=={json.dumps(str(value)+separator+"%", ensure_ascii=False)});\n'
        body += 'assert(CLog::errors.empty());assert(CGUIControlSliderSetting::GetText(integer,CVariant("invalid"),0,1,100,&loc).empty());}\n'
    return body + 'std::cout<<"PASS: production double/integer slider formatting, EN/DE localization and old-label logged fallback\\n";}\n'


def run(text, negative=False):
    with tempfile.TemporaryDirectory(prefix='subtitle-percentage-format-') as temporary:
        out = Path(temporary)
        (out / 'test.cpp').write_text(text)
        # Compile failure is never accepted as a rejected behavioral mutant.
        subprocess.run([os.environ.get('CXX', 'g++'), '-std=c++17', '-DFMT_HEADER_ONLY', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-fno-pie', '-no-pie',
                        '-I', str(FMT), '-I', str(ROOT / 'xbmc'), str(out / 'test.cpp'), '-o', str(out / 'test')], check=True)
        result = subprocess.run([str(out / 'test')], capture_output=True, text=True,
                                env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=0'}, timeout=10)
        if negative:
            assert result.returncode == -signal.SIGABRT and 'Assertion' in result.stderr and 'AddressSanitizer' not in result.stderr and 'runtime error:' not in result.stderr, result.stdout + result.stderr
        else:
            assert result.returncode == 0, result.stdout + result.stderr
            print(result.stdout.strip())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--negative-controls', action='store_true')
    parser.add_argument('--fmt-include', type=Path, help='fmt include directory; use configured CE dependency for target-version verification')
    args = parser.parse_args()
    global FMT
    if args.fmt_include is not None:
        FMT = args.fmt_include
    assert (FMT / 'fmt/format.h').is_file(), 'fmt headers unavailable: supply --fmt-include or CE_FMT_INCLUDE'
    print('fmt include:', FMT)
    labels, languages = configuration()
    run(code(labels, languages))
    # Explicitly exercise multiline PO parsing, independent of current line wrapping.
    parsed = po('msgctxt "#1"\nmsgid "{0:"\n".1f} %"\nmsgstr "{0:.1f}"\n" %"\n')
    assert parsed['#1']['msgid'] == parsed['#1']['msgstr'] == '{0:.1f} %'
    try:
        po('msgctxt "#1"\nmsgid "a"\nmsgstr ""\n\nmsgctxt "#1"\nmsgid "b"\nmsgstr ""\n')
    except AssertionError:
        pass
    else:
        raise AssertionError('duplicate PO fixture not rejected')
    print('PASS: unchanged number ranges/defaults/dependency, global/dialog wiring, shared integer labels, multiline PO and duplicate IDs')
    if args.negative_controls:
        for name in IDS:
            mutated = dict(labels)
            mutated[name] = 14047
            run(code(mutated, languages), True)
            print('REJECTED runtime:', name + ' restored integer formatlabel')
        for label, target in [('new percentage uses integer format', 69365), ('shared integer label changed', 14047)]:
            mutated = copy.deepcopy(languages)
            for strings in mutated.values():
                strings[target] = '{0:d} %' if target == 69365 else '{0:.1f} %'
            run(code(labels, mutated), True)
            print('REJECTED runtime:', label)


if __name__ == '__main__':
    main()
