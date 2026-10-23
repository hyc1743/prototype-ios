// SPDX-License-Identifier: MIT
// Migrate only named, simple literals previously shipped as factory defaults.
#pragma once
#include <initializer_list>
#include <string>
#include <string_view>

struct PrototypeFactoryValue {
  std::string_view key, previous, replacement;
};

inline std::string PrototypeMigrateFactoryValues(
    std::string text, std::initializer_list<PrototypeFactoryValue> values) {
  const std::string newline = text.find("\r\n") != std::string::npos ? "\r\n" : "\n";
  for (const auto& value : values) {
    bool found = false;
    size_t line = 0;
    while (line < text.size()) {
      size_t end = text.find('\n', line);
      if (end == std::string::npos) end = text.size();
      size_t key = text.find_first_not_of(" \t", line);
      if (key < end && text.compare(key, value.key.size(), value.key) == 0) {
        size_t equals = key + value.key.size();
        while (equals < end && (text[equals] == ' ' || text[equals] == '\t')) ++equals;
        if (equals < end && text[equals] == '=') {
          found = true;
          size_t first = equals + 1;
          while (first < end && (text[first] == ' ' || text[first] == '\t')) ++first;
          size_t last = text.find('#', first);
          if (last == std::string::npos || last > end) last = end;
          while (last > first && (text[last - 1] == ' ' || text[last - 1] == '\t' || text[last - 1] == '\r')) --last;
          if (std::string_view(text).substr(first, last - first) == value.previous) {
            const size_t old_size = last - first;
            text.replace(first, old_size, value.replacement);
            end = end - old_size + value.replacement.size();
          }
        }
      }
      line = end < text.size() ? end + 1 : text.size();
    }
    if (!found) {
      if (!text.empty() && text.back() != '\n') text += newline;
      text += std::string(value.key) + " = " + std::string(value.replacement) + newline;
    }
  }
  return text;
}
