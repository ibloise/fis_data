library(tidyverse)

source("R/readers.R")


# Notas:
# Desarrollar lineas temporales en unidades claves (UCIs, URgencias, Hemato)
# Desarrollar los estudios por episodio también desagregados por unidades.
# Añadir también el análisis del delta Ct. 
#   

data <- read_clean_data() %>% 
  distinct()


data %>% 
  filter(microorg == "Klebsiella oxytoca", carba_encoded_org %in% c("0000", "0100")) %>%
  arrange(sample) %>% 
  View()

microorg_carba <- data %>% 
  filter(microorg != 0) %>% 
  group_by(microorg, carba_encoded_org) %>% 
  summarise(n=n()) %>% 
  arrange(desc(n)) 


data_cross <- data %>% 
  select(nhc, sample, microorg, carba_encoded_org, carba_encoded_fis)

data_cross <- left_join(data_cross, microorg_carba, by=c("microorg", "carba_encoded_org"))

nhcs <- data_cross %>% 
  filter(n==1) %>% 
  select(nhc) %>% 
  pull


data %>% 
  filter(nhc %in% nhcs) %>% 
  arrange(nhc, entry_date) %>% 
  View()
