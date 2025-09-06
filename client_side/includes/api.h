#ifndef API_H
#define API_H

#include "types.h"
#include "config.h"

// ---------- GLOBAL DATA ----------
extern int breads_id[MAX_KEYS];
extern int bread_cook_time[MAX_KEYS];
extern int data_count;
extern int bread_buffer[MAX_KEYS];
extern int bread_buffer_count;
extern volatile bool init_success;

// ---------- API FUNCTIONS ----------
bool fetchInitData();
int apiNewCustomer(const std::vector<int>& breads);
NextTicketResponse apiNextTicket(int customer_ticket_id);
CurrentTicketResponse apiCurrentTicket();
bool apiSkipTicket(int customer_ticket_id);

// ---------- STORAGE FUNCTIONS ----------
void saveInitDataToFlash();

#endif
