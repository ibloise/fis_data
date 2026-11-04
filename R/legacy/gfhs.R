library(tidyverse)
library(here)

source("R/readers.R")


# Notas:
# Desarrollar lineas temporales en unidades claves (UCIs, URgencias, Hemato)
# Desarrollar los estudios por episodio también desagregados por unidades.
# Añadir también el análisis del delta Ct. 
#   


data <- read_clean_data()

# Hay que preparar una función para sacar esta tabla siempre que haga falta

positives_carbas_gfhs <- data %>% 
  filter(!is.na(nhc)) %>% 
  select(sample, origin_code, desc_proc, ends_with("specific")) %>%
  select(-encode_carba_specific) %>% 
  filter(!is.na(OXA48_specific)) %>% 
  distinct()


data_fis_long <- positives_carbas_gfhs %>% 
  pivot_longer(cols = ends_with("_specific"), names_to = "carbapenemasa", values_to = "presence")

top_servicios <- data_fis_long %>%
  group_by(sample, origin_code, desc_proc) %>% 
  summarise(
    has_carba = any(presence == 1),
    .groups = "drop"
  ) %>% 
  filter(has_carba) %>% 
  count(origin_code, sort = TRUE) %>% 
  slice_head(n = 20)

top_servicios

top_list <- top_servicios %>% 
  select(origin_code) %>% 
  distinct() %>% 
  pull()

levels_order <- c("OXA48", "VIM", "KPC", "NDM")


gfhs_dist <- data_fis_long %>% 
  filter(presence != 0, origin_code %in% top_list) %>% 
  mutate(
    carbapenemasa = str_replace(carbapenemasa, "_specific", ""),
    carbapenemasa = factor(carbapenemasa, levels = levels_order),
    origin_code = factor(origin_code, levels = top_list),
    desc_proc = enc2utf8(desc_proc)
  ) %>% 
  ggplot(aes(x = carbapenemasa, fill = carbapenemasa)) +
  geom_bar() +
  facet_wrap(~ desc_proc, labeller = label_wrap_gen(width = 20)) +
  theme_bw()

ggsave(here("img", "gfhs.png"), gfhs_dist)
