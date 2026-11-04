library(tidyverse)
library(here)


# Valida clean data

source(here("R", "readers.R"))
source(here("R", "transform.R"))

data <- read_clean_data()

data_withoud_mo <- withdraw_organism_in_clean_data(data)


# Data no debe tener negativos ni non proccess

invalid_samples <- data %>%
  filter(result_interpretate %in% c("Negative culture", "Non-process culture")) %>%
  group_by(sample) %>%
  summarise(
    n = n(),
    n_types = n_distinct(result_interpretate),
    values = paste(unique(result_interpretate), collapse = ", "),
    .groups = "drop"
  ) %>%
  filter(n > 1 | n_types > 1)

invalid_samples

data %>% 
  filter(!is.na(microorg_code), result_interpretate == "Positive Culture") %>% 
  group_by(sample, microorg_code) %>% 
  filter(n()>1)

# Vamos a enviar los registros sin resultado a una tabla para poder revisarlos

lost_samples <-data %>% 
  filter(is.na(nhc)) %>% 
  select(sample) %>% 
  distinct()

write.csv(lost_samples, here("data", "lost_samples.csv"))
