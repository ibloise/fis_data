# Desactualizado. Hay que reprogramar los chequeos y validaciones

library(tidyverse)
library(here)

source(here("R", "readers.R"))

final_df <- read_clean_data()

patients_swabs_positives <- final_df %>% 
  filter(carba_encoded_fis != "0000") %>% 
  select(nhc, sample, culture_result) %>% 
  distinct() %>% 
  group_by(nhc, culture_result) %>% 
  summarise(n=n())

patients_swabs_positives <- patients_swabs_by_result %>% 
  filter(culture_result == "Positive Culture", n>1) %>% 
  select(nhc) %>% pull()

swabs_positives_greaterone <- final_df %>% 
  filter(nhc %in% patients_swabs_positives, culture_result == "Positive Culture", carba_encoded_fis != "0000") %>% 
  arrange(nhc, entry_date)


swabs_positives_greaterone %>% 
  arrange(nhc, entry_date) %>% 
  group_by(nhc) %>% 
  mutate(sample_order = row_number()) %>% 
  ggplot(aes(x = sample_order, y = carba_encoded_fis, group = nhc)) +
  geom_line(alpha = 0.1)


patients_swabs_by_result %>% 
  filter(culture_result == "Positive Culture", n>15)